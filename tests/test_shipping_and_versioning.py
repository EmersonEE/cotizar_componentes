import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.models import Product, Customer
from src.core.calculator import QuoteCalculator, format_currency
from src.core.exporter import QuoteExporter
from src.core.history_manager import HistoryManager
from src.config import AppConfig

def test_shipping_and_versioning():
    print("--- INICIANDO TEST DE REGLAS DE ENVÍO Y VERSIONADO ---")
    config = AppConfig.load()
    history_mgr = HistoryManager()
    exporter = QuoteExporter()

    # 1. Test Items:
    # La Electrónica (Q80 < Q150 -> debe cobrar envío Q35)
    p_la = Product(name="Sensor Ultrasónico HC-SR04", url="https://laelectronica.com.gt/products/sensor", store_name="La Electrónica", unit_price=40.0)
    item_la = QuoteCalculator.create_quote_item(p_la, quantity=2) # 80.00

    # Electrónica DIY (Q775 >= Q250 -> Envío Gratis)
    p_diy = Product(name="FNIRSI HRM-10 Tester", url="https://www.electronicadiy.com/products/tester", store_name="Electrónica DIY", unit_price=775.0)
    item_diy = QuoteCalculator.create_quote_item(p_diy, quantity=1) # 775.00

    # RyCH (Q2.25 -> Retiro en tienda)
    p_rych = Product(name="Cable Calibre 22", url="https://electronicarych.com/shop/cable", store_name="Electrónica RyCH", unit_price=2.25)
    item_rych = QuoteCalculator.create_quote_item(p_rych, quantity=1) # 2.25

    items = [item_la, item_diy, item_rych]
    customer = Customer(name="Ing. Emerson Estrada", phone="+502 5555-1234")

    # Evaluate shipping
    store_subtotals = QuoteCalculator.calculate_store_subtotals(items)
    print(f"Subtotales por tienda: {store_subtotals}")
    assert store_subtotals["La Electrónica"] == 80.0
    assert store_subtotals["Electrónica DIY"] == 775.0
    assert store_subtotals["Electrónica RyCH"] == 2.25

    shipping_details = QuoteCalculator.evaluate_shipping_details(
        store_subtotals,
        config.shipping_rules,
        custom_shipping_costs={"La Electrónica": 35.0}
    )

    for sd in shipping_details:
        print(f"  • {sd.store_name}: Subtotal={sd.items_subtotal}, Costo={sd.shipping_cost}, Status={sd.status_label}")

    la_sd = next(s for s in shipping_details if s.store_name == "La Electrónica")
    diy_sd = next(s for s in shipping_details if s.store_name == "Electrónica DIY")
    rych_sd = next(s for s in shipping_details if s.store_name == "Electrónica RyCH")

    assert la_sd.shipping_cost == 35.0
    assert not la_sd.qualifies_free
    assert diy_sd.shipping_cost == 0.0
    assert diy_sd.qualifies_free
    assert rych_sd.shipping_cost == 0.0
    assert rych_sd.is_pickup_only

    # Build Quote v1
    quote_id = history_mgr.get_next_quote_id("COT")
    quote_v1 = QuoteCalculator.build_quote(
        quote_id=quote_id,
        items=items,
        customer=customer,
        shipping_details=shipping_details,
        service_fee_percent=12.0
    )

    expected_items_subtotal = 80.0 + 775.0 + 2.25 # 857.25
    expected_fee = round(expected_items_subtotal * 0.12, 2) # 102.87
    expected_total = round(expected_items_subtotal + expected_fee + 35.0, 2) # 995.12

    print(f"\nCotización v1 ({quote_v1.quote_id}):")
    print(f"  Subtotal Componentes: {format_currency(quote_v1.items_subtotal)}")
    print(f"  Margen (12%):         {format_currency(quote_v1.service_fee_amount)}")
    print(f"  Total Envíos:         {format_currency(quote_v1.total_shipping)}")
    print(f"  TOTAL GENERAL:        {format_currency(quote_v1.total)}")

    assert abs(quote_v1.items_subtotal - expected_items_subtotal) < 0.01
    assert abs(quote_v1.service_fee_amount - expected_fee) < 0.01
    assert abs(quote_v1.total_shipping - 35.0) < 0.01
    assert abs(quote_v1.total - expected_total) < 0.01

    history_mgr.save_quote(quote_v1)
    exporter.export_all(quote_v1, config.business)

    # 2. Test Versioning: Create Quote v2
    new_qid, new_version, base_id = history_mgr.get_next_version_info(quote_v1.quote_id)
    print(f"\nGenerando nueva versión: {new_qid} (Versión {new_version}, Base {base_id})")
    assert new_version == 2
    assert new_qid == f"{quote_v1.quote_id}_v2"

    # In v2: change La Electrónica quantity to 4 (4 * 40 = Q160 >= Q150 -> Free Shipping!)
    item_la_v2 = QuoteCalculator.create_quote_item(p_la, quantity=4) # 160.00
    items_v2 = [item_la_v2, item_diy, item_rych]
    
    store_subtotals_v2 = QuoteCalculator.calculate_store_subtotals(items_v2)
    shipping_details_v2 = QuoteCalculator.evaluate_shipping_details(store_subtotals_v2, config.shipping_rules)

    la_sd_v2 = next(s for s in shipping_details_v2 if s.store_name == "La Electrónica")
    assert la_sd_v2.qualifies_free
    assert la_sd_v2.shipping_cost == 0.0

    quote_v2 = QuoteCalculator.build_quote(
        quote_id=new_qid,
        items=items_v2,
        customer=customer,
        shipping_details=shipping_details_v2,
        service_fee_percent=12.0,
        version=new_version,
        base_quote_id=base_id
    )

    print(f"\nCotización v2 ({quote_v2.quote_id}):")
    print(f"  Subtotal Componentes: {format_currency(quote_v2.items_subtotal)}")
    print(f"  Margen (12%):         {format_currency(quote_v2.service_fee_amount)}")
    print(f"  Total Envíos:         {format_currency(quote_v2.total_shipping)}")
    print(f"  TOTAL GENERAL:        {format_currency(quote_v2.total)}")

    history_mgr.save_quote(quote_v2)
    csv_f, html_f, pdf_f = exporter.export_all(quote_v2, config.business)

    assert csv_f.exists()
    assert html_f.exists()
    if pdf_f:
        assert pdf_f.exists()
        print(f"  [OK] PDF v2 generado exitosamente: {pdf_f.name}")

    # Check both versions in history
    all_quotes = history_mgr.load_all_quotes()
    quote_ids = [q.quote_id for q in all_quotes]
    assert quote_v1.quote_id in quote_ids
    assert quote_v2.quote_id in quote_ids

    print("\n--- TODOS LOS TESTS DE ENVÍO Y VERSIONADO PASARON CON ÉXITO ---")

if __name__ == "__main__":
    test_shipping_and_versioning()
