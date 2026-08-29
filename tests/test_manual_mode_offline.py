import sys
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.models import Product, QuoteItem, Customer, BusinessInfo
from src.core.calculator import QuoteCalculator
from src.core.history_manager import HistoryManager
from src.core.exporter import QuoteExporter
from src.scrapers.base import ScraperError

class TestManualModeOffline(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.history_file = Path(self.test_dir) / "test_history.json"
        self.output_dir = Path(self.test_dir) / "output"
        self.history_mgr = HistoryManager(file_path=self.history_file)
        self.exporter = QuoteExporter(output_dir=self.output_dir)
        self.customer = Customer(name="Cliente Manual Test", phone="55554444", email="manual@test.com", notes="Prueba manual")
        self.business = BusinessInfo(name="Mi Negocio", owner="Owner", phone="1234", email="info@negocio.com")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_manual_product_creation_and_serialization(self):
        """Validates that a manual product retains all properties, sku, and is_manual flag."""
        prod = Product(
            name="Transformador 12V 2A",
            url="",
            store_name="Proveedor Local",
            unit_price=45.0,
            in_stock=True,
            stock_status="Disponible",
            sku="TR-12V2A",
            is_manual=True
        )

        item = QuoteCalculator.create_quote_item(prod, quantity=3)
        self.assertEqual(item.subtotal, 135.0)

        # Test dictionary serialization and deserialization
        item_dict = item.to_dict()
        self.assertTrue(item_dict["product"]["is_manual"])
        self.assertEqual(item_dict["product"]["sku"], "TR-12V2A")
        self.assertEqual(item_dict["product"]["url"], "")

        restored_item = QuoteItem.from_dict(item_dict)
        self.assertTrue(restored_item.product.is_manual)
        self.assertEqual(restored_item.product.sku, "TR-12V2A")
        self.assertEqual(restored_item.subtotal, 135.0)

    @patch('src.core.history_manager.scrape_product')
    def test_manual_product_without_url_is_not_scraped_during_reverification(self, mock_scrape):
        """Validates that manual products or products without URLs are preserved without calling scraper."""
        # Mix of regular product and manual product
        p_regular = Product("ESP32", "https://example.com/esp32", "La Electrónica", 80.0, is_manual=False)
        p_manual_no_url = Product("Transformador", "", "Proveedor Local", 50.0, sku="TR-01", is_manual=True)
        p_manual_with_url = Product("Sensor Especial", "https://other.com/sensor", "Electrónica DIY", 30.0, is_manual=True)

        items = [
            QuoteCalculator.create_quote_item(p_regular, 1),
            QuoteCalculator.create_quote_item(p_manual_no_url, 2),
            QuoteCalculator.create_quote_item(p_manual_with_url, 1)
        ]

        quote = QuoteCalculator.build_quote("COT-MAN-001", items, self.customer)
        self.history_mgr.save_quote(quote)

        # Mock scraper response for regular product (price changed from 80 -> 90)
        mock_scrape.return_value = Product("ESP32", "https://example.com/esp32", "La Electrónica", 90.0, is_manual=False)

        candidate_q, changes, diff = self.history_mgr.check_quote_price_updates("COT-MAN-001")

        # mock_scrape should only have been called ONCE (for regular product), NOT for the manual ones
        self.assertEqual(mock_scrape.call_count, 1)
        mock_scrape.assert_called_once_with("https://example.com/esp32")

        # Manual items must be preserved in candidate quote
        self.assertEqual(candidate_q.items[1].product.name, "Transformador")
        self.assertEqual(candidate_q.items[1].unit_price, 50.0)
        self.assertTrue(candidate_q.items[1].product.is_manual)

        self.assertEqual(candidate_q.items[2].product.name, "Sensor Especial")
        self.assertEqual(candidate_q.items[2].unit_price, 30.0)
        self.assertTrue(candidate_q.items[2].product.is_manual)

    @patch('src.core.history_manager.scrape_product')
    def test_fallen_store_during_reverification_does_not_crash(self, mock_scrape):
        """Validates that if a store is down (scraper raises exception), reverification handles it gracefully."""
        from src.config import AppConfig
        config = AppConfig.load()
        p = Product("Arduino Uno", "https://example.com/arduino", "Electrónica DIY", 120.0)
        # Envío explícito según las reglas reales para que el total se conserve si el precio no cambia
        shipping = QuoteCalculator.evaluate_shipping_details(
            {"Electrónica DIY": 120.0}, config.shipping_rules
        )
        quote = QuoteCalculator.build_quote(
            "COT-FAIL-001",
            [QuoteCalculator.create_quote_item(p, 1)],
            self.customer,
            shipping_details=shipping,
        )
        self.history_mgr.save_quote(quote)

        # Simulate store down (e.g. 503 Service Unavailable or Connection Timeout)
        mock_scrape.side_effect = ScraperError("503 Service Unavailable: Tienda fuera de línea")

        candidate_q, changes, diff = self.history_mgr.check_quote_price_updates("COT-FAIL-001")

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["stock_status"], "Tienda no disponible")
        self.assertEqual(candidate_q.items[0].unit_price, 120.0)
        self.assertEqual(candidate_q.total, quote.total)

    def test_dual_export_manual_product_rendering(self):
        """
        Validates dual export:
        - Internal HTML contains '⚠️ Manual' and 'SKU' tags.
        - Client HTML does NOT contain internal technical warnings or 'Manual' badges.
        """
        p_manual = Product(
            name="Bobina de Cobre 10m",
            url="",
            store_name="Proveedor Local",
            unit_price=25.0,
            sku="BOB-10M",
            is_manual=True
        )
        quote = QuoteCalculator.build_quote("COT-EXP-MAN", [QuoteCalculator.create_quote_item(p_manual, 2)], self.customer)

        exp_res = self.exporter.export_all(quote, self.business)

        # 1. Check Internal HTML
        with open(exp_res.internal_html, "r", encoding="utf-8") as f:
            int_content = f.read()
        self.assertIn("⚠️ Manual", int_content)
        self.assertIn("SKU: BOB-10M", int_content)

        # 2. Check Client HTML
        with open(exp_res.client_html, "r", encoding="utf-8") as f:
            cli_content = f.read()
        self.assertNotIn("⚠️ Manual", cli_content)
        self.assertNotIn("class=\"manual-badge\"", cli_content)
        self.assertIn("Bobina de Cobre 10m", cli_content)

        # 3. Check CSV
        with open(exp_res.csv, "r", encoding="utf-8-sig") as f:
            csv_content = f.read()
        self.assertIn("BOB-10M", csv_content)
        self.assertIn("SÍ", csv_content)

if __name__ == "__main__":
    unittest.main()
