import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.health_check import (
    KNOWN_PRODUCT_URLS,
    check_store_product,
    check_store_search,
    run_store_health_check,
)
from src.models import Product
from src.scrapers.search import SearchResultItem


class TestHealthCheckOffline(unittest.TestCase):

    def test_known_urls_for_all_stores(self):
        """El registro central de tiendas tiene URL de prueba configurada para cada una."""
        from src.stores import STORE_NAMES
        self.assertEqual(set(KNOWN_PRODUCT_URLS.keys()), set(STORE_NAMES))

    @patch("src.health_check.scrape_product")
    def test_check_product_ok(self, mock_scrape):
        mock_scrape.return_value = Product(
            "ESP32 DevKit", "https://x.com/esp32", "La Electrónica", 95.0, in_stock=True, stock_status="Disponible"
        )
        res = check_store_product("La Electrónica", "https://x.com/esp32")
        self.assertTrue(res["ok"])
        self.assertEqual(res["price"], 95.0)
        self.assertEqual(res["stock"], "Disponible")
        self.assertGreaterEqual(res["latency_s"], 0.0)

    @patch("src.health_check.scrape_product")
    def test_check_product_failure(self, mock_scrape):
        mock_scrape.side_effect = RuntimeError("Producto no encontrado (HTTP 404)")
        res = check_store_product("La Electrónica", "https://x.com/esp32")
        self.assertFalse(res["ok"])
        self.assertIn("404", res["error"])

    @patch("src.health_check.scrape_product")
    def test_check_product_zero_price_is_failure(self, mock_scrape):
        mock_scrape.return_value = Product("X", "https://x.com/x", "La Electrónica", 0.0)
        res = check_store_product("La Electrónica", "https://x.com/x")
        self.assertFalse(res["ok"])

    def test_check_product_missing_url(self):
        res = check_store_product("La Electrónica", "")
        self.assertFalse(res["ok"])
        self.assertIn("Sin URL", res["error"])

    @patch("src.scrapers.search.search_la_electronica")
    def test_check_search_ok(self, mock_search):
        mock_search.return_value = [
            SearchResultItem(store_name="La Electrónica", title="ESP32 A", url="https://x/1",
                             unit_price=90.0, in_stock=True, stock_status="Disponible"),
            SearchResultItem(store_name="La Electrónica", title="ESP32 B", url="https://x/2",
                             unit_price=0.0, in_stock=True, stock_status="Disponible"),
        ]
        res = check_store_search("La Electrónica")
        self.assertTrue(res["ok"])
        self.assertEqual(res["results"], 1)  # solo el de precio > 0

    @patch("src.scrapers.search.search_la_electronica")
    def test_check_search_failure(self, mock_search):
        mock_search.side_effect = RuntimeError("timeout")
        res = check_store_search("La Electrónica")
        self.assertFalse(res["ok"])
        self.assertIn("timeout", res["error"])

    def test_check_search_unknown_store(self):
        res = check_store_search("Tienda Inexistente")
        self.assertFalse(res["ok"])

    @patch("src.scrapers.search.search_electronica_rych")
    @patch("src.scrapers.search.search_la_electronica")
    @patch("src.scrapers.search.search_electronica_diy")
    @patch("src.health_check.scrape_product")
    def test_run_full_check_reports_all_stores(self, mock_scrape, m_diy, m_la, m_rych):
        mock_scrape.return_value = Product("ESP32", "https://x.com/e", "La Electrónica", 95.0)
        ok_item = [SearchResultItem(store_name="La Electrónica", title="ESP32", url="https://x/e",
                                    unit_price=95.0, in_stock=True, stock_status="Disponible")]
        m_rych.return_value = ok_item
        m_la.return_value = ok_item
        m_diy.return_value = ok_item

        results = run_store_health_check()
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r["overall_ok"] for r in results))
        stores = {r["store_name"] for r in results}
        self.assertEqual(stores, {"La Electrónica", "Electrónica DIY", "Electrónica RyCH"})


if __name__ == "__main__":
    unittest.main()
