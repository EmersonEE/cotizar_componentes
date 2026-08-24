import sys
import os
from pathlib import Path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.models import Product, QuoteItem, Quote, Customer, BusinessInfo
from src.core.calculator import QuoteCalculator
from src.core.exporter import QuoteExporter, ExportResult

def test_dual_export():
    print("--- INICIANDO TEST DE EXPORTACIÓN DUAL (CLIENTE vs INTERNA) ---")
    
    exporter = QuoteExporter()
    
    # Create test products from DIY (with variant), RyCH, and La Electrónica
    p1 = Product(
        name="Resistencias por unidad Ohms 1/4W (220)",
        url="https://www.electronicadiy.com/products/resistencias-por-unidad-1-4w?variant=33598010130571",
        store_name="Electrónica DIY",
        unit_price=0.50
    )
    p2 = Product(
        name="MD-ESP32 Modulo Wifi + Bluetooth",
        url="https://electronicarych.com/shop/md-esp32-md-esp32-modulo-wifi-bluetooth-2-1-cpu-de-doble-nucleo-esp32z-12381",
        store_name="Electrónica RyCH",
        unit_price=99.50
    )
    p3 = Product(
        name="Board de desarrollo Wi-Fi ESP32",
        url="https://laelectronica.com.gt/products/board-de-desarrollo-wifi-bt-esp32-ch340",
        store_name="La Electrónica",
        unit_price=120.00
    )

    items = [
        QuoteCalculator.create_quote_item(p1, 10),
        QuoteCalculator.create_quote_item(p2, 1),
        QuoteCalculator.create_quote_item(p3, 1),
    ]

    customer = Customer(name="Ing. Emerson Test", phone="+502 5555-1234")
    
    store_subtotals = QuoteCalculator.calculate_store_subtotals(items)
    shipping_rules = {
        "Electrónica RyCH": {"is_pickup_only": True},
        "La Electrónica": {"free_threshold": 150.0, "default_cost": 35.0},
        "Electrónica DIY": {"free_threshold": 250.0, "default_cost": 35.0}
    }
    shipping_details = QuoteCalculator.evaluate_shipping_details(store_subtotals, shipping_rules)

    quote = QuoteCalculator.build_quote(
        quote_id="COT-TEST-DUAL",
        items=items,
        customer=customer,
        shipping_details=shipping_details,
        service_fee_percent=12.0
    )

    business = BusinessInfo(
        name="Emerson Electrónica & Integración",
        owner="Emerson",
        phone="+502 5555-5555",
        email="ventas@emerson.gt"
    )

    # 1. Test export_all
    exp_res: ExportResult = exporter.export_all(quote, business)

    print(f"  [OK] CSV generado:           {exp_res.csv} (Existe: {exp_res.csv.exists()})")
    print(f"  [OK] HTML Cliente generado:  {exp_res.client_html} (Existe: {exp_res.client_html.exists()})")
    print(f"  [OK] PDF Cliente generado:   {exp_res.client_pdf} (Existe: {exp_res.client_pdf.exists() if exp_res.client_pdf else False})")
    print(f"  [OK] HTML Interno generado:  {exp_res.internal_html} (Existe: {exp_res.internal_html.exists()})")
    print(f"  [OK] PDF Interno generado:   {exp_res.internal_pdf} (Existe: {exp_res.internal_pdf.exists() if exp_res.internal_pdf else False})")

    assert exp_res.csv.exists(), "CSV debe existir"
    assert exp_res.client_html.exists(), "HTML Cliente debe existir"
    assert exp_res.internal_html.exists(), "HTML Interno debe existir"
    assert exp_res.client_pdf and exp_res.client_pdf.exists(), "PDF Cliente debe existir"
    assert exp_res.internal_pdf and exp_res.internal_pdf.exists(), "PDF Interno debe existir"

    # 2. Check HTML contents
    client_html_str = exp_res.client_html.read_text(encoding="utf-8")
    internal_html_str = exp_res.internal_html.read_text(encoding="utf-8")

    # Client HTML should NOT have href to product URLs in item names
    assert p1.url not in client_html_str, "El HTML de cliente no debe contener enlaces a tiendas externas"
    assert "🔒 Control Interno" not in client_html_str, "El HTML de cliente no debe tener badge interno"

    # Internal HTML MUST have href to exact product URLs
    assert p1.url in internal_html_str, "El HTML interno debe contener la URL exacta con ?variant=..."
    assert p2.url in internal_html_str, "El HTML interno debe contener la URL de RyCH"
    assert p3.url in internal_html_str, "El HTML interno debe contener la URL de La Electrónica"
    assert "item-link" in internal_html_str, "El HTML interno debe usar la clase item-link"
    assert "🔒 Control Interno" in internal_html_str, "El HTML interno debe mostrar el badge de control interno"

    # 3. Check Backward Compatibility Unpacking
    c, h, p = exp_res
    assert c == exp_res.csv
    assert h == exp_res.client_html
    assert p == exp_res.client_pdf

    print("\n--- TODOS LOS TESTS DE EXPORTACIÓN DUAL PASARON EXITOSAMENTE ---")

if __name__ == "__main__":
    test_dual_export()
