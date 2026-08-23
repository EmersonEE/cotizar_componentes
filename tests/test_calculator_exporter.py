import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.models import Product, Customer
from src.core.calculator import QuoteCalculator, format_currency
from src.core.exporter import QuoteExporter
from src.core.history_manager import HistoryManager
from src.config import AppConfig

def test_full_pipeline():
    print("--- INICIANDO TEST DE CÁLCULO, HISTORIAL Y EXPORTACIÓN ---")
    
    # 1. Dummy Products
    p1 = Product(name="Multímetro ZOYI ZT-225", url="https://laelectronica.com.gt/products/test1", store_name="La Electrónica", unit_price=799.00)
    p2 = Product(name="FNIRSI HRM-10 Baterías", url="https://www.electronicadiy.com/products/test2", store_name="Electrónica DIY", unit_price=775.00)
    p3 = Product(name="Alambre Calibre 22 1mt", url="https://electronicarych.com/shop/test3", store_name="Electrónica RyCH", unit_price=2.25)

    item1 = QuoteCalculator.create_quote_item(p1, quantity=1)
    item2 = QuoteCalculator.create_quote_item(p2, quantity=2)
    item3 = QuoteCalculator.create_quote_item(p3, quantity=10)

    # Subtotal check: 799*1 + 775*2 + 2.25*10 = 799 + 1550 + 22.5 = 2371.50
    expected_subtotal = 799.00 + 1550.00 + 22.50
    expected_fee = round(expected_subtotal * 0.12, 2)
    expected_total = round(expected_subtotal + expected_fee, 2)

    customer = Customer(name="Ing. Carlos Mendoza", phone="+502 4433-2211", email="cmendoza@universidad.edu.gt")
    
    history_mgr = HistoryManager()
    quote_id = history_mgr.get_next_quote_id("COT")

    quote = QuoteCalculator.build_quote(
        quote_id=quote_id,
        items=[item1, item2, item3],
        customer=customer,
        service_fee_percent=12.0,
        validity_days=5
    )

    print(f"ID Cotización: {quote.quote_id}")
    print(f"Subtotal:      {format_currency(quote.subtotal)}")
    print(f"Margen 12%:    {format_currency(quote.service_fee_amount)}")
    print(f"Total:         {format_currency(quote.total)}")

    assert abs(quote.subtotal - expected_subtotal) < 0.01
    assert abs(quote.service_fee_amount - expected_fee) < 0.01
    assert abs(quote.total - expected_total) < 0.01

    # Save to history
    history_mgr.save_quote(quote)
    retrieved = history_mgr.get_quote(quote_id)
    assert retrieved is not None
    assert len(retrieved.items) == 3

    # Export CSV, HTML, PDF
    config = AppConfig.load()
    exporter = QuoteExporter()
    csv_f, html_f, pdf_f = exporter.export_all(quote, config.business)

    print(f"  [OK] CSV generado:  {csv_f} (Existe: {csv_f.exists()})")
    print(f"  [OK] HTML generado: {html_f} (Existe: {html_f.exists()})")
    print(f"  [OK] PDF generado:  {pdf_f} (Existe: {pdf_f.exists() if pdf_f else False})")

    assert csv_f.exists()
    assert html_f.exists()
    if pdf_f:
        assert pdf_f.exists()
        print(f"  [OK] Tamaño del PDF: {pdf_f.stat().st_size} bytes")

    print("\n--- TEST DE CÁLCULO Y EXPORTACIÓN FINALIZADO CON ÉXITO ---")

if __name__ == "__main__":
    test_full_pipeline()
