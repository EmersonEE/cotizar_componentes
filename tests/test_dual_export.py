import sys
import os
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.models import Product, Customer, BusinessInfo
from src.core.calculator import QuoteCalculator
from src.core.exporter import QuoteExporter, ExportResult


class TestDualExportOffline(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.output_dir = Path(self.test_dir) / "output"
        self.exporter = QuoteExporter(output_dir=self.output_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_dual_export_client_vs_internal(self):
        p1 = Product(
            name="Resistencias por unidad Ohms 1/4W (220)",
            url="https://www.electronicadiy.com/products/resistencias-por-unidad-1-4w?variant=33598010130571",
            store_name="Electrónica DIY", unit_price=0.50,
        )
        p2 = Product(
            name="MD-ESP32 Modulo Wifi + Bluetooth",
            url="https://electronicarych.com/shop/md-esp32-md-esp32-modulo-wifi-bluetooth-2-1-cpu-de-doble-nucleo-esp32z-12381",
            store_name="Electrónica RyCH", unit_price=99.50,
        )
        p3 = Product(
            name="Board de desarrollo Wi-Fi ESP32",
            url="https://laelectronica.com.gt/products/board-de-desarrollo-wifi-bt-esp32-ch340",
            store_name="La Electrónica", unit_price=120.00,
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
            "Electrónica DIY": {"free_threshold": 250.0, "default_cost": 35.0},
        }
        shipping_details = QuoteCalculator.evaluate_shipping_details(store_subtotals, shipping_rules)

        quote = QuoteCalculator.build_quote(
            quote_id="COT-TEST-DUAL",
            items=items,
            customer=customer,
            shipping_details=shipping_details,
            service_fee_percent=12.0,
        )

        business = BusinessInfo(name="Emerson Electrónica & Integración", phone="+502 5555-5555")

        exp_res: ExportResult = self.exporter.export_all(quote, business)

        self.assertTrue(exp_res.csv.exists())
        self.assertTrue(exp_res.client_html.exists())
        self.assertTrue(exp_res.internal_html.exists())
        if exp_res.client_pdf:
            self.assertTrue(exp_res.client_pdf.exists())
        if exp_res.internal_pdf:
            self.assertTrue(exp_res.internal_pdf.exists())

        client_html_str = exp_res.client_html.read_text(encoding="utf-8")
        internal_html_str = exp_res.internal_html.read_text(encoding="utf-8")

        # Cliente: sin enlaces a tiendas externas ni badge interno
        self.assertNotIn(p1.url, client_html_str)
        self.assertNotIn("🔒 Control Interno", client_html_str)

        # Interna: URLs exactas (con ?variant=), clase item-link y badge interno
        self.assertIn(p1.url, internal_html_str)
        self.assertIn(p2.url, internal_html_str)
        self.assertIn(p3.url, internal_html_str)
        self.assertIn("item-link", internal_html_str)
        self.assertIn("🔒 Control Interno", internal_html_str)

        # Desempaquetado backward-compatible: csv, html, pdf
        c, h, p = exp_res
        self.assertEqual(c, exp_res.csv)
        self.assertEqual(h, exp_res.client_html)
        self.assertEqual(p, exp_res.client_pdf)


if __name__ == "__main__":
    unittest.main()
