import sys
import os
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.scrapers import search as search_module
from src.scrapers.search import metasearch, SearchResultItem


def _item(store: str, title: str, price: float):
    return SearchResultItem(
        store_name=store, title=title, url=f"https://example.com/{store}/{title}",
        unit_price=price, in_stock=True, stock_status="Disponible",
    )


class TestMetasearchOffline(unittest.TestCase):

    def setUp(self):
        search_module._CACHE.clear()

    @patch("src.scrapers.search.search_electronica_sigma",
           return_value=[_item("Electrónica Sigma", "ESP32 SIGMA", 88.0)])
    @patch("src.scrapers.search.search_electronica_diy",
           return_value=[_item("Electrónica DIY", "ESP32 DIY", 90.0)])
    @patch("src.scrapers.search.search_la_electronica",
           return_value=[_item("La Electrónica", "ESP32 LA", 95.0)])
    @patch("src.scrapers.search.search_electronica_rych",
           return_value=[_item("Electrónica RyCH", "ESP32 RYCH", 99.0)])
    def test_metasearch_combines_stores(self, m_rych, m_la, m_diy, m_sigma):
        res = metasearch("ESP32")
        self.assertEqual(len(res), 4)
        stores = {r.store_name for r in res}
        self.assertEqual(stores, {"Electrónica DIY", "La Electrónica", "Electrónica RyCH", "Electrónica Sigma"})

    @patch("src.scrapers.search.search_electronica_sigma",
           return_value=[_item("Electrónica Sigma", "X SIGMA", 4.0)])
    @patch("src.scrapers.search.search_electronica_diy",
           return_value=[_item("Electrónica DIY", "X DIY", 1.0)])
    @patch("src.scrapers.search.search_la_electronica",
           return_value=[_item("La Electrónica", "X LA", 2.0)])
    @patch("src.scrapers.search.search_electronica_rych",
           return_value=[_item("Electrónica RyCH", "X RYCH", 3.0)])
    def test_metasearch_cache_avoids_repeated_calls(self, m_rych, m_la, m_diy, m_sigma):
        query = "cache-test-xyz"
        r1 = metasearch(query)
        r2 = metasearch(query)
        self.assertEqual(len(r1), 4)
        self.assertEqual(len(r2), 4)
        # El caché (F10) evita volver a golpear las tiendas
        for mock in (m_rych, m_la, m_diy, m_sigma):
            mock.assert_called_once()

    @patch("src.scrapers.search.search_electronica_sigma", return_value=[])
    @patch("src.scrapers.search.search_electronica_diy", return_value=[])
    @patch("src.scrapers.search.search_la_electronica", return_value=[])
    @patch("src.scrapers.search.search_electronica_rych")
    def test_metasearch_global_timeout_bounds_wall_time(self, m_rych, m_la, m_diy, m_sigma):
        def slow(*args, **kwargs):
            time.sleep(0.8)
            return []

        for mock in (m_rych, m_la, m_diy, m_sigma):
            mock.side_effect = slow

        t0 = time.time()
        res = metasearch("timeout-test", global_timeout=0.3)
        elapsed = time.time() - t0
        self.assertEqual(res, [])
        self.assertLess(elapsed, 2.5)


if __name__ == "__main__":
    unittest.main()
