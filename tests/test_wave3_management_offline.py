import sys
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.models import Product, Customer, QuoteStatus
from src.core.calculator import QuoteCalculator
from src.core.history_manager import HistoryManager


def _item(name="ESP32", price=50.0, url="https://x.com/esp32", store="La Electrónica", sku=None):
    return QuoteCalculator.create_quote_item(
        Product(name=name, url=url, store_name=store, unit_price=price, sku=sku), 1
    )


class TestEffectiveStatusF1(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.mgr = HistoryManager(file_path=Path(self.test_dir) / "history.json")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _quote(self, quote_id="COT-2026-0001", status=QuoteStatus.GUARDADA.value, valid_until=None):
        q = QuoteCalculator.build_quote(
            quote_id=quote_id, items=[_item()], customer=Customer("Cliente"),
            shipping_details=[], service_fee_percent=10.0,
        )
        q.status = status
        if valid_until is not None:
            q.valid_until = valid_until
        return q

    def test_expired_guardada_becomes_vencida(self):
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
        q = self._quote(valid_until=yesterday)
        self.assertEqual(self.mgr.effective_status(q), QuoteStatus.VENCIDA.value)

    def test_future_validity_keeps_status(self):
        future = (datetime.now() + timedelta(days=3)).strftime("%d/%m/%Y")
        q = self._quote(valid_until=future)
        self.assertEqual(self.mgr.effective_status(q), QuoteStatus.GUARDADA.value)

    def test_accepted_never_auto_expires(self):
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
        q = self._quote(status=QuoteStatus.ACEPTADA.value, valid_until=yesterday)
        self.assertEqual(self.mgr.effective_status(q), QuoteStatus.ACEPTADA.value)

    def test_invalid_date_keeps_status(self):
        q = self._quote(valid_until="no-es-una-fecha")
        self.assertEqual(self.mgr.effective_status(q), QuoteStatus.GUARDADA.value)

    def test_filter_vencida_includes_expired_quotes(self):
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
        expired = self._quote(quote_id="COT-2026-0001", valid_until=yesterday)
        fresh = self._quote(quote_id="COT-2026-0002")
        self.mgr.save_quote(expired)
        self.mgr.save_quote(fresh)

        res = self.mgr.search_quotes(status_filter="VENCIDA")
        self.assertEqual([q.quote_id for q in res], ["COT-2026-0001"])


class TestDeleteQuoteF2(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.mgr = HistoryManager(file_path=Path(self.test_dir) / "history.json")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_delete_removes_quote_and_versions(self):
        q1 = QuoteCalculator.build_quote("COT-2026-0001", [_item()], Customer("C"), shipping_details=[])
        q1v2 = QuoteCalculator.build_quote(
            "COT-2026-0001_v2", [_item(price=60.0)], Customer("C"), shipping_details=[],
            version=2, base_quote_id="COT-2026-0001",
        )
        q2 = QuoteCalculator.build_quote("COT-2026-0002", [_item()], Customer("C"), shipping_details=[])
        self.mgr.save_quote(q1)
        self.mgr.save_quote(q1v2)
        self.mgr.save_quote(q2)

        removed = self.mgr.delete_quote("COT-2026-0001")
        self.assertEqual(removed, 2)  # base + versión
        remaining = self.mgr.load_all_quotes()
        self.assertEqual([q.quote_id for q in remaining], ["COT-2026-0002"])

    def test_delete_missing_returns_zero(self):
        self.assertEqual(self.mgr.delete_quote("COT-9999-0001"), 0)


class TestDedupeItemsF8(unittest.TestCase):

    def test_merge_by_url_sums_quantity(self):
        p = Product("ESP32", "https://x.com/esp32", "La Electrónica", 50.0)
        merged = QuoteCalculator.merge_duplicate_items([
            QuoteCalculator.create_quote_item(p, 1),
            QuoteCalculator.create_quote_item(p, 2),
        ])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].quantity, 3)
        self.assertEqual(merged[0].subtotal, 150.0)

    def test_merge_by_sku_even_with_different_urls(self):
        p1 = Product("Cable A", "https://x.com/a", "La Electrónica", 10.0, sku="CB-1")
        p2 = Product("Cable B", "https://x.com/b", "La Electrónica", 10.0, sku="cb-1")  # SKU case-insensitive
        merged = QuoteCalculator.merge_duplicate_items([
            QuoteCalculator.create_quote_item(p1, 1),
            QuoteCalculator.create_quote_item(p2, 2),
        ])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].quantity, 3)

    def test_order_preserved_and_distinct_kept(self):
        pa = Product("A", "https://x.com/a", "La Electrónica", 10.0)
        pb = Product("B", "https://x.com/b", "La Electrónica", 20.0)
        merged = QuoteCalculator.merge_duplicate_items([
            QuoteCalculator.create_quote_item(pa, 1),
            QuoteCalculator.create_quote_item(pb, 1),
            QuoteCalculator.create_quote_item(pa, 4),
        ])
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0].product.name, "A")
        self.assertEqual(merged[0].quantity, 5)
        self.assertEqual(merged[1].product.name, "B")

    def test_search_by_sku(self):
        self.test_dir = tempfile.mkdtemp()
        try:
            mgr = HistoryManager(file_path=Path(self.test_dir) / "history.json")
            q = QuoteCalculator.build_quote(
                "COT-2026-0001", [_item(sku="TR-12V2A")], Customer("Cliente"), shipping_details=[]
            )
            mgr.save_quote(q)
            res = mgr.search_quotes("TR-12V2A")
            self.assertEqual(len(res), 1)
            self.assertEqual(res[0].quote_id, "COT-2026-0001")
        finally:
            shutil.rmtree(self.test_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
