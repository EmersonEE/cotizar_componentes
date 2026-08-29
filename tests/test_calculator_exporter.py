import sys
import os
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.models import Product, Customer, BusinessInfo
from src.core.calculator import QuoteCalculator, format_currency
from src.core.exporter import QuoteExporter
from src.core.history_manager import HistoryManager


class TestCalculatorExporterOffline(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.history_file = Path(self.test_dir) / "test_history.json"
        self.output_dir = Path(self.test_dir) / "output"
        self.history_mgr = HistoryManager(file_path=self.history_file)
        self.exporter = QuoteExporter(output_dir=self.output_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_full_pipeline(self):
        p1 = Product(name="Multímetro ZOYI ZT-225", url="https://laelectronica.com.gt/products/test1",
                     store_name="La Electrónica", unit_price=799.00)
        p2 = Product(name="FNIRSI HRM-10 Baterías", url="https://www.electronicadiy.com/products/test2",
                     store_name="Electrónica DIY", unit_price=775.00)
        p3 = Product(name="Alambre Calibre 22 1mt", url="https://electronicarych.com/shop/test3",
                     store_name="Electrónica RyCH", unit_price=2.25)

        items = [
            QuoteCalculator.create_quote_item(p1, quantity=1),
            QuoteCalculator.create_quote_item(p2, quantity=2),
            QuoteCalculator.create_quote_item(p3, quantity=10),
        ]

        expected_subtotal = 799.00 + 1550.00 + 22.50
        expected_fee = round(expected_subtotal * 0.12, 2)
        expected_total = round(expected_subtotal + expected_fee, 2)

        customer = Customer(name="Ing. Carlos Mendoza", phone="+502 4433-2211", email="cmendoza@universidad.edu.gt")

        quote_id = self.history_mgr.get_next_quote_id("COT")
        quote = QuoteCalculator.build_quote(
            quote_id=quote_id,
            items=items,
            customer=customer,
            service_fee_percent=12.0,
            validity_days=5,
        )

        self.assertAlmostEqual(quote.subtotal, expected_subtotal, delta=0.01)
        self.assertAlmostEqual(quote.service_fee_amount, expected_fee, delta=0.01)
        self.assertAlmostEqual(quote.total, expected_total, delta=0.01)

        # Guardar en historial (sin tocar el archivo real de la app)
        self.history_mgr.save_quote(quote)
        retrieved = self.history_mgr.get_quote(quote_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(len(retrieved.items), 3)

        # Exportación CSV + HTML + PDF (Cliente e Interna)
        biz = BusinessInfo(name="Emerson Electrónica & Integración")
        exp_res = self.exporter.export_all(quote, biz)

        self.assertTrue(exp_res.csv.exists())
        self.assertTrue(exp_res.client_html.exists())
        self.assertTrue(exp_res.internal_html.exists())
        if exp_res.client_pdf:
            self.assertTrue(exp_res.client_pdf.exists())
        if exp_res.internal_pdf:
            self.assertTrue(exp_res.internal_pdf.exists())

        # Desempaquetado backward-compatible
        c, h, p = exp_res
        self.assertEqual(c, exp_res.csv)
        self.assertEqual(h, exp_res.client_html)
        self.assertEqual(p, exp_res.client_pdf)

        self.assertEqual(format_currency(1234.5), "Q 1,234.50")


if __name__ == "__main__":
    unittest.main()
