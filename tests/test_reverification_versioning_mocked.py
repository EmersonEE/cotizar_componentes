import sys
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.models import (
    Product, Customer, StoreShippingDetail
)
from src.core.calculator import QuoteCalculator
from src.core.history_manager import HistoryManager
from src.core.exporter import QuoteExporter

class TestReverificationVersioningMocked(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.history_file = Path(self.test_dir) / "test_history.json"
        self.output_dir = Path(self.test_dir) / "output"
        self.history_mgr = HistoryManager(file_path=self.history_file)
        self.exporter = QuoteExporter(output_dir=self.output_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch('src.core.history_manager.scrape_product')
    def test_reverification_preserves_original_and_generates_candidate(self, mock_scrape):
        """
        Validates that check_quote_price_updates does NOT modify history.json
        and creates a candidate new version with accurate deltas.
        """
        # Setup original quote in history
        p1 = Product("ESP32 NodeMCU", "https://example.com/esp32", "Electrónica DIY", 80.0, in_stock=True, stock_status="Disponible")
        p2 = Product("Sensor DHT22", "https://example.com/dht22", "La Electrónica", 50.0, in_stock=True, stock_status="Disponible")
        
        items = [
            QuoteCalculator.create_quote_item(p1, 2), # 160.0
            QuoteCalculator.create_quote_item(p2, 1)  # 50.0
        ] # Total items subtotal = 210.0

        shipping = [
            StoreShippingDetail("Electrónica DIY", 160.0, 250.0, False, 35.0, "Q 35.00", False, True),
            StoreShippingDetail("La Electrónica", 50.0, 150.0, False, 35.0, "Q 35.00", False, True)
        ] # Total shipping = 70.0

        original = QuoteCalculator.build_quote(
            quote_id="COT-2026-0001",
            items=items,
            customer=Customer(name="Carlos Ramos"),
            shipping_details=shipping,
            service_fee_percent=10.0,
            version=1
        )
        # items_subtotal = 210.0, fee = 21.0, shipping = 70.0, total = 301.0
        self.history_mgr.save_quote(original)

        # Mock scraped updates:
        # ESP32 went from 80.0 -> 90.0 (in stock)
        # DHT22 went from 50.0 -> 40.0 (out of stock)
        def side_effect(url):
            if "esp32" in url:
                return Product("ESP32 NodeMCU", url, "Electrónica DIY", 90.0, in_stock=True, stock_status="Disponible")
            elif "dht22" in url:
                return Product("Sensor DHT22", url, "La Electrónica", 40.0, in_stock=False, stock_status="Agotado")
            raise ValueError(f"Unexpected url {url}")

        mock_scrape.side_effect = side_effect

        # 1. Run check_quote_price_updates
        candidate_q, changes, diff = self.history_mgr.check_quote_price_updates("COT-2026-0001")

        # 2. Check that original quote in storage is 100% UNMODIFIED
        stored_orig = self.history_mgr.get_quote("COT-2026-0001")
        self.assertEqual(stored_orig.version, 1)
        self.assertEqual(stored_orig.items_subtotal, 210.0)
        self.assertEqual(stored_orig.total, 301.0)
        self.assertEqual(len(self.history_mgr.load_all_quotes()), 1)

        # 3. Validate Candidate quote attributes
        self.assertEqual(candidate_q.quote_id, "COT-2026-0001_v2")
        self.assertEqual(candidate_q.version, 2)
        self.assertEqual(candidate_q.base_quote_id, "COT-2026-0001")
        self.assertEqual(candidate_q.customer.name, "Carlos Ramos")
        # New items subtotal: 2*90 + 1*40 = 180 + 40 = 220.0
        self.assertEqual(candidate_q.items_subtotal, 220.0)
        # New fee: 10% of 220.0 = 22.0
        self.assertEqual(candidate_q.service_fee_amount, 22.0)
        # New shipping: 70.0
        self.assertEqual(candidate_q.total_shipping, 70.0)
        # New total: 220 + 22 + 70 = 312.0
        self.assertEqual(candidate_q.total, 312.0)

        # 4. Validate Summary Diff
        self.assertEqual(diff["old_items_subtotal"], 210.0)
        self.assertEqual(diff["new_items_subtotal"], 220.0)
        self.assertEqual(diff["items_subtotal_diff"], 10.0)
        self.assertEqual(diff["old_service_fee"], 21.0)
        self.assertEqual(diff["new_service_fee"], 22.0)
        self.assertEqual(diff["service_fee_diff"], 1.0)
        self.assertEqual(diff["old_total"], 301.0)
        self.assertEqual(diff["new_total"], 312.0)
        self.assertEqual(diff["total_diff"], 11.0)

        # Stock alert for DHT22
        self.assertEqual(len(diff["stock_alerts"]), 1)
        self.assertEqual(diff["stock_alerts"][0]["product_name"], "Sensor DHT22")

    @patch('src.core.history_manager.scrape_product')
    def test_save_reverified_version_persists_both_versions(self, mock_scrape):
        """Validates that accepting reverification saves new version and keeps v1."""
        p = Product("Relay 5V", "https://example.com/relay", "Electrónica RyCH", 20.0)
        orig = QuoteCalculator.build_quote(
            quote_id="COT-2026-0005",
            items=[QuoteCalculator.create_quote_item(p, 1)],
            customer=Customer("Cliente"),
            version=1
        )
        self.history_mgr.save_quote(orig)

        mock_scrape.return_value = Product("Relay 5V", "https://example.com/relay", "Electrónica RyCH", 25.0)

        candidate_q, _, _ = self.history_mgr.check_quote_price_updates("COT-2026-0005")
        self.history_mgr.save_reverified_version(candidate_q)

        all_quotes = self.history_mgr.load_all_quotes()
        self.assertEqual(len(all_quotes), 2)

        v1 = self.history_mgr.get_quote("COT-2026-0005")
        v2 = self.history_mgr.get_quote("COT-2026-0005_v2")

        self.assertIsNotNone(v1)
        self.assertIsNotNone(v2)
        self.assertEqual(v1.version, 1)
        self.assertEqual(v1.items_subtotal, 20.0)
        self.assertEqual(v2.version, 2)
        self.assertEqual(v2.base_quote_id, "COT-2026-0005")
        self.assertEqual(v2.items_subtotal, 25.0)

    @patch('src.core.history_manager.scrape_product')
    def test_custom_shipping_costs_preserved_during_reverification(self, mock_scrape):
        """Validates that customized shipping fees set on original quote are preserved."""
        p = Product("Cable", "https://example.com/cable", "La Electrónica", 30.0)
        custom_shipping = [
            StoreShippingDetail("La Electrónica", 30.0, 150.0, False, 50.0, "Q 50.00 (Especial)", False, True)
        ]
        orig = QuoteCalculator.build_quote(
            quote_id="COT-2026-0008",
            items=[QuoteCalculator.create_quote_item(p, 1)],
            customer=Customer("Cliente"),
            shipping_details=custom_shipping
        )
        self.history_mgr.save_quote(orig)

        mock_scrape.return_value = Product("Cable", "https://example.com/cable", "La Electrónica", 35.0)

        cand, _, _ = self.history_mgr.check_quote_price_updates("COT-2026-0008")
        self.assertEqual(cand.shipping_details[0].shipping_cost, 50.0)
        self.assertEqual(cand.total_shipping, 50.0)

    @patch('src.core.history_manager.scrape_product')
    def test_cancel_reverification_leaves_history_intact(self, mock_scrape):
        """Validates that cancelling leaves history completely unchanged."""
        p = Product("Item", "https://example.com/item", "Electrónica RyCH", 10.0)
        orig = QuoteCalculator.build_quote("COT-2026-0009", [QuoteCalculator.create_quote_item(p, 1)], Customer("C"))
        self.history_mgr.save_quote(orig)

        mock_scrape.return_value = Product("Item", "https://example.com/item", "Electrónica RyCH", 99.0)

        # Only check without saving
        candidate_q, _, _ = self.history_mgr.check_quote_price_updates("COT-2026-0009")

        # Simulate cancellation: do not call save_reverified_version
        all_quotes = self.history_mgr.load_all_quotes()
        self.assertEqual(len(all_quotes), 1)
        self.assertEqual(all_quotes[0].quote_id, "COT-2026-0009")
        self.assertEqual(all_quotes[0].items_subtotal, 10.0)

if __name__ == "__main__":
    unittest.main()
