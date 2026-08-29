from unittest.mock import patch

from src.models import Product, Customer, QuoteStatus
from src.core.calculator import QuoteCalculator
from src.core.exporter import QuoteExporter
from src.core.history_manager import HistoryManager
from src.scrapers.search import search_la_electronica_single_term, search_electronica_diy_single_term


def test_t1_search_price_extraction_thousands():
    """T1: Ensure prices >= 1,000 GTQ (e.g. Q 1,250.00) are correctly parsed."""
    html_sample_la = """
    <div class="card-wrapper">
        <a class="full-unstyled-link" href="/products/osciloscopio-digital">Osciloscopio Digital 100MHz</a>
        <div class="price"><span class="price-item--regular">Q 1,250.00</span></div>
    </div>
    """
    with patch("src.scrapers.search.robust_fetch_html", return_value=html_sample_la):
        results = search_la_electronica_single_term("osciloscopio")
        assert len(results) == 1
        assert results[0].unit_price == 1250.0

    html_sample_diy = """
    <div class="productitem">
        <a href="/products/estacion-soldadura-pro"></a>
        <h2 class="productitem--title">Estación de Soldadura Pro</h2>
        <span class="price__current">Q 2,499.50</span>
    </div>
    """
    with patch("src.scrapers.search.robust_fetch_html", return_value=html_sample_diy):
        results = search_electronica_diy_single_term("estacion")
        assert len(results) == 1
        assert results[0].unit_price == 2499.50


def test_t2_price_history_chronological_sort(tmp_path):
    """T2: Ensure get_price_history sorts by real datetime rather than string sorting."""
    history_file = tmp_path / "history.json"
    mgr = HistoryManager(file_path=history_file)

    p = Product(name="ESP32 WROOM", url="https://electronicarych.com/esp32", store_name="Electrónica RyCH", unit_price=45.0)
    
    # Quote from January 15, 2026 (string "15/01/2026")
    q1 = QuoteCalculator.build_quote(
        quote_id="COT-2026-0001",
        items=[QuoteCalculator.create_quote_item(p, 1)],
        customer=Customer(name="Cliente 1"),
        service_fee_percent=10.0
    )
    q1.date = "15/01/2026"
    mgr.save_quote(q1)

    # Quote from August 02, 2026 (string "02/08/2026") - Lexicographically "02/08/2026" < "15/01/2026"
    p2 = Product(name="ESP32 WROOM", url="https://electronicarych.com/esp32", store_name="Electrónica RyCH", unit_price=55.0)
    q2 = QuoteCalculator.build_quote(
        quote_id="COT-2026-0002",
        items=[QuoteCalculator.create_quote_item(p2, 1)],
        customer=Customer(name="Cliente 2"),
        service_fee_percent=10.0
    )
    q2.date = "02/08/2026"
    mgr.save_quote(q2)

    hist = mgr.get_price_history(url="https://electronicarych.com/esp32", limit=5)
    assert len(hist) == 2
    # The most recent quote chronologically should be August 02, 2026 (55.0)
    assert hist[0]["quote_id"] == "COT-2026-0002"
    assert hist[0]["unit_price"] == 55.0
    assert hist[1]["quote_id"] == "COT-2026-0001"
    assert hist[1]["unit_price"] == 45.0


def test_f2_frequent_customers(tmp_path):
    """F2: Ensure frequent customers are correctly aggregated and sorted."""
    mgr = HistoryManager(file_path=tmp_path / "history.json")
    cust_a = Customer(name="Ing. Carlos Ramos", phone="5544-3322", email="carlos@empresa.gt")
    cust_b = Customer(name="Lic. Ana Gomez", phone="4433-2211")

    p = Product(name="Sensor", url="https://url", store_name="La Electrónica", unit_price=20.0)

    for i in range(3):
        q = QuoteCalculator.build_quote(
            quote_id=f"COT-2026-000{i+1}",
            items=[QuoteCalculator.create_quote_item(p, 2)],
            customer=cust_a
        )
        mgr.save_quote(q)

    q_b = QuoteCalculator.build_quote(
        quote_id="COT-2026-0004",
        items=[QuoteCalculator.create_quote_item(p, 1)],
        customer=cust_b
    )
    mgr.save_quote(q_b)

    frequent = mgr.get_frequent_customers()
    assert len(frequent) == 2
    assert frequent[0]["name"] == "Ing. Carlos Ramos"
    assert frequent[0]["count"] == 3
    assert frequent[0]["phone"] == "5544-3322"
    assert frequent[1]["name"] == "Lic. Ana Gomez"
    assert frequent[1]["count"] == 1


def test_f1_commercial_analytics(tmp_path):
    """F1: Ensure commercial analytics KPIs and conversion rates are accurately computed."""
    mgr = HistoryManager(file_path=tmp_path / "history.json")
    p = Product(name="Kit Arduino", url="https://url", store_name="Electrónica RyCH", unit_price=100.0)

    # 1 Accepted quote
    q1 = QuoteCalculator.build_quote(
        quote_id="COT-2026-0001",
        items=[QuoteCalculator.create_quote_item(p, 1)],
        customer=Customer(name="Cliente Ganado"),
        service_fee_percent=10.0
    )
    q1.status = QuoteStatus.ACEPTADA.value
    mgr.save_quote(q1)

    # 1 Sent quote
    q2 = QuoteCalculator.build_quote(
        quote_id="COT-2026-0002",
        items=[QuoteCalculator.create_quote_item(p, 2)],
        customer=Customer(name="Cliente Pendiente"),
        service_fee_percent=10.0
    )
    q2.status = QuoteStatus.ENVIADA.value
    mgr.save_quote(q2)

    stats = mgr.get_commercial_analytics()
    assert stats["total_quotes"] == 2
    assert stats["accepted_count"] == 1
    assert stats["conversion_rate"] == 50.0
    assert stats["total_sold_amount"] == q1.total
    assert stats["total_earned_margin"] == q1.service_fee_amount
    assert stats["status_counts"][QuoteStatus.ACEPTADA.value] == 1


def test_f4_quote_discounts(tmp_path):
    """F4: Test special commercial discounts in calculation, quote serialization and export."""
    p1 = Product(name="ESP32", url="https://url", store_name="Electrónica RyCH", unit_price=100.0)
    item = QuoteCalculator.create_quote_item(p1, 2)  # Subtotal = 200.0

    # 10% discount on items subtotal (Discount = 20.0, Subtotal after discount = 180.0)
    # Service fee 10% on 180.0 = 18.0. Total = 180.0 + 18.0 + 0 = 198.0
    quote = QuoteCalculator.build_quote(
        quote_id="COT-2026-0099",
        items=[item],
        customer=Customer(name="Cliente VIP"),
        discount_percent=10.0,
        service_fee_percent=10.0,
        shipping_rules={}
    )

    assert quote.items_subtotal == 200.0
    assert quote.discount_percent == 10.0
    assert quote.discount_amount == 20.0
    assert quote.service_fee_amount == 18.0
    assert quote.total == 198.0

    # Test serialization
    data = quote.to_dict()
    assert data["discount_percent"] == 10.0
    assert data["discount_amount"] == 20.0

    # Test exporter output
    exporter = QuoteExporter(output_dir=tmp_path / "output")
    html_content = exporter.render_html_string(quote)
    assert "Descuento Especial" in html_content
    assert "20.00" in html_content

    csv_path = exporter.export_csv(quote)
    csv_text = csv_path.read_text(encoding="utf-8-sig")
    assert "DESCUENTO ESPECIAL" in csv_text


def test_f5_packing_list_generation(tmp_path):
    """F5: Ensure packing list aggregates items by store and calculates disbursements."""
    p_rych = Product(name="ESP32", url="https://rych.com/esp32", store_name="Electrónica RyCH", unit_price=50.0, sku="RYCH-01")
    p_la = Product(name="Sensor DHT22", url="https://la.com/dht22", store_name="La Electrónica", unit_price=40.0, sku="LA-02")
    
    items = [
        QuoteCalculator.create_quote_item(p_rych, 2), # 100.0
        QuoteCalculator.create_quote_item(p_la, 1),   # 40.0
    ]
    
    quote = QuoteCalculator.build_quote(
        quote_id="COT-2026-0050",
        items=items,
        customer=Customer(name="Integrador Pro"),
        service_fee_percent=10.0, # 14.0 fee
        custom_shipping_costs={"La Electrónica": 15.0} # 15.0 shipping
    )

    exporter = QuoteExporter(output_dir=tmp_path / "output")
    packing_list = exporter.generate_packing_list(quote)

    assert packing_list["quote_id"] == "COT-2026-0050"
    assert "Electrónica RyCH" in packing_list["stores"]
    assert "La Electrónica" in packing_list["stores"]
    assert len(packing_list["stores"]["Electrónica RyCH"]["items"]) == 1
    assert len(packing_list["stores"]["La Electrónica"]["items"]) == 1
    
    # Store disbursements:
    # RyCH = 100.0 + 0 = 100.0
    # La = 40.0 + 15.0 = 55.0
    # Total purchase cost = 155.0
    # Client price = 140 (items) + 14 (fee) + 15 (ship) = 169.0
    # Profit = 169.0 - 155.0 = 14.0
    assert packing_list["total_purchase_cost"] == 155.0
    assert packing_list["total_client_price"] == 169.0
    assert packing_list["estimated_profit"] == 14.0


def test_bom_manual_candidate_override():
    """Test assigning a manual candidate or direct URL to an unfound BOM line."""
    from src.core.bom_parser import ParsedBOMItem
    from src.core.bom_searcher import MatchResult, SearchResultItem, build_all_bom_scenarios
    from src.config import AppConfig

    # Initially not found item
    bom_item = ParsedBOMItem(raw_line="1x Caja organizadora pequeña", quantity=1, product_query="Caja organizadora pequeña")
    m = MatchResult(bom_item=bom_item, best_match=None, all_candidates=[], confidence_score=0.0, status="NO_ENCONTRADO")
    assert m.selected_candidate is None
    assert m.status == "NO_ENCONTRADO"

    # Assign manual replacement item
    manual_cand = SearchResultItem(
        store_name="La Electrónica",
        title="Caja Plástica Organizadora 10 Divisiones",
        url="https://laelectronica.com.gt/products/caja-10",
        unit_price=25.0,
        in_stock=True,
        stock_status="Disponible"
    )
    m.selected_candidate = manual_cand
    m.all_candidates.insert(0, (manual_cand, 1.0))
    m.confidence_score = 1.0
    m.status = "ALTA"
    m.is_confirmed = True

    assert m.selected_candidate.title == "Caja Plástica Organizadora 10 Divisiones"
    assert m.selected_candidate.unit_price == 25.0

    # Build scenarios with this manually assigned match
    cfg = AppConfig.load()
    scenarios = build_all_bom_scenarios(
        match_results=[m],
        customer=Customer(name="Test Manual"),
        config=cfg,
        service_fee_percent=10.0
    )
    assert len(scenarios) >= 4
    mixed = scenarios[0]
    assert mixed.total_found == 1
    assert len(mixed.items) == 1
    assert mixed.items[0].product.name == "Caja Plástica Organizadora 10 Divisiones"
    assert mixed.items[0].unit_price == 25.0
