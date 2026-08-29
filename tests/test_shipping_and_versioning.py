import sys
import os
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.models import Product, Customer
from src.core.calculator import QuoteCalculator
from src.core.history_manager import HistoryManager
from src.core.exporter import QuoteExporter


class TestShippingAndVersioningOffline(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.history_file = Path(self.test_dir) / "test_history.json"
        self.output_dir = Path(self.test_dir) / "output"
        self.history_mgr = HistoryManager(file_path=self.history_file)
        self.exporter = QuoteExporter(output_dir=self.output_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_shipping_thresholds_and_versioning(self):
        test_shipping_rules = {
            "La Electrónica": {"free_threshold": 150.0, "default_cost": 35.0, "is_pickup_only": False},
            "Electrónica DIY": {"free_threshold": 250.0, "default_cost": 35.0, "is_pickup_only": False},
            "Electrónica RyCH": {"free_threshold": None, "default_cost": 0.0, "is_pickup_only": True},
        }

        p_la = Product("Tester Component La Electronica", "https://laelectronica.com.gt/products/test",
                       "La Electrónica", 40.00)
        p_diy = Product("Tester Component DIY", "https://electronicadiy.com/products/test",
                        "Electrónica DIY", 775.00)
        p_rych = Product("Tester Component RyCH", "https://electronicarych.com/shop/test",
                         "Electrónica RyCH", 2.25)

        # La Electrónica: 2 x 40 = 80 (< 150) -> 35.00
        # DIY: 1 x 775 = 775 (>= 250) -> gratis
        # RyCH: 1 x 2.25 = 2.25 (retiro) -> 0
        items = [
            QuoteCalculator.create_quote_item(p_la, quantity=2),
            QuoteCalculator.create_quote_item(p_diy, quantity=1),
            QuoteCalculator.create_quote_item(p_rych, quantity=1),
        ]
        customer = Customer("Cliente Versiones", "4455-6677")

        store_subtotals = QuoteCalculator.calculate_store_subtotals(items)
        self.assertEqual(store_subtotals["La Electrónica"], 80.0)
        self.assertEqual(store_subtotals["Electrónica DIY"], 775.0)
        self.assertEqual(store_subtotals["Electrónica RyCH"], 2.25)

        shipping_details = QuoteCalculator.evaluate_shipping_details(store_subtotals, test_shipping_rules)
        by_store = {sd.store_name: sd for sd in shipping_details}

        self.assertEqual(by_store["La Electrónica"].shipping_cost, 35.0)
        self.assertFalse(by_store["La Electrónica"].qualifies_free)
        self.assertEqual(by_store["Electrónica DIY"].shipping_cost, 0.0)
        self.assertTrue(by_store["Electrónica DIY"].qualifies_free)
        self.assertEqual(by_store["Electrónica RyCH"].shipping_cost, 0.0)
        self.assertTrue(by_store["Electrónica RyCH"].is_pickup_only)

        # v1
        quote_id = self.history_mgr.get_next_quote_id("COT")
        quote_v1 = QuoteCalculator.build_quote(
            quote_id=quote_id, items=items, customer=customer,
            shipping_details=shipping_details, service_fee_percent=12.0,
        )
        expected_items_subtotal = 857.25
        expected_fee = round(expected_items_subtotal * 0.12, 2)
        self.assertAlmostEqual(quote_v1.total_shipping, 35.0, delta=0.01)
        self.assertAlmostEqual(quote_v1.total, expected_items_subtotal + expected_fee + 35.0, delta=0.01)

        self.history_mgr.save_quote(quote_v1)

        # v2: cambia cantidad de La Electrónica a 4 -> 160 >= 150 -> gratis
        new_qid, new_version, base_id = self.history_mgr.get_next_version_info(quote_v1.quote_id)
        self.assertEqual(new_version, 2)
        self.assertEqual(new_qid, f"{quote_v1.quote_id}_v2")

        items_v2 = [
            QuoteCalculator.create_quote_item(p_la, quantity=4),
            QuoteCalculator.create_quote_item(p_diy, quantity=1),
            QuoteCalculator.create_quote_item(p_rych, quantity=1),
        ]
        shipping_v2 = QuoteCalculator.evaluate_shipping_details(
            QuoteCalculator.calculate_store_subtotals(items_v2), test_shipping_rules
        )
        la_v2 = next(sd for sd in shipping_v2 if sd.store_name == "La Electrónica")
        self.assertTrue(la_v2.qualifies_free)
        self.assertEqual(la_v2.shipping_cost, 0.0)

        quote_v2 = QuoteCalculator.build_quote(
            quote_id=new_qid, items=items_v2, customer=customer,
            shipping_details=shipping_v2, service_fee_percent=12.0,
            version=new_version, base_quote_id=base_id,
        )
        self.assertAlmostEqual(quote_v2.total_shipping, 0.0, delta=0.01)
        self.history_mgr.save_quote(quote_v2)

        self.assertEqual(len(self.history_mgr.load_all_quotes()), 2)


if __name__ == "__main__":
    unittest.main()
