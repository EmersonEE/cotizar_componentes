import sys
import os
import json
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.models import Product, Customer, QuoteStatus
from src.core.calculator import QuoteCalculator
from src.core.history_manager import HistoryManager


class TestPriceHistoryF4(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.mgr = HistoryManager(file_path=Path(self.test_dir) / "history.json")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_price_history_by_url_and_sku(self):
        prod = Product("ESP32", "https://x.com/esp32", "La Electrónica", 80.0, sku="ESP-1")
        q1 = QuoteCalculator.build_quote(
            "COT-2026-0001", [QuoteCalculator.create_quote_item(prod, 1)], Customer("C"), shipping_details=[]
        )
        q1.date = "01/08/2026"
        prod2 = Product("ESP32", "https://x.com/esp32", "La Electrónica", 90.0)
        q2 = QuoteCalculator.build_quote(
            "COT-2026-0002", [QuoteCalculator.create_quote_item(prod2, 1)], Customer("C"), shipping_details=[]
        )
        q2.date = "15/08/2026"
        self.mgr.save_quote(q1)
        self.mgr.save_quote(q2)

        hist = self.mgr.get_price_history(url="https://x.com/esp32")
        self.assertEqual(len(hist), 2)
        # Ordenado por fecha desc
        self.assertEqual(hist[0]["quote_id"], "COT-2026-0002")
        self.assertEqual(hist[0]["unit_price"], 90.0)
        self.assertEqual(hist[1]["quote_id"], "COT-2026-0001")

        hist_sku = self.mgr.get_price_history(sku="ESP-1")
        self.assertEqual(len(hist_sku), 1)

        self.assertEqual(self.mgr.get_price_history(url="https://otro.com/x"), [])
        self.assertEqual(self.mgr.get_price_history(), [])


class TestSaleNotesF6(unittest.TestCase):

    def test_sale_notes_roundtrip(self):
        prod = Product("Sensor", "https://x.com/sensor", "La Electrónica", 30.0)
        q = QuoteCalculator.build_quote(
            "COT-2026-0001", [QuoteCalculator.create_quote_item(prod, 1)], Customer("C"), shipping_details=[]
        )
        q.status = QuoteStatus.ACEPTADA.value
        q.sale_notes = "Factura FAC-123, entregado 15/08"

        restored = type(q).from_dict(q.to_dict())
        self.assertEqual(restored.sale_notes, "Factura FAC-123, entregado 15/08")

        # Legacy: sin sale_notes -> vacío
        legacy = {"quote_id": "COT-2026-0009", "customer": {}, "items": [], "shipping_details": [],
                  "subtotal": 0.0, "total": 0.0}
        self.assertEqual(type(q).from_dict(legacy).sale_notes, "")


class TestExportImportHistoryF7(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.mgr = HistoryManager(file_path=Path(self.test_dir) / "history.json")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _quote(self, qid, name="Cliente"):
        prod = Product("P", f"https://x.com/{qid}", "La Electrónica", 50.0)
        return QuoteCalculator.build_quote(
            qid, [QuoteCalculator.create_quote_item(prod, 1)], Customer(name), shipping_details=[]
        )

    def test_export_and_import_json(self):
        q1 = self._quote("COT-2026-0001")
        q2 = self._quote("COT-2026-0002", "Cliente B")
        self.mgr.save_quote(q1)
        self.mgr.save_quote(q2)

        export_path = Path(self.test_dir) / "backup.json"
        self.mgr.export_history(export_path)
        self.assertTrue(export_path.exists())
        with open(export_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(data), 2)

        # Importar en un historial vacío
        other = HistoryManager(file_path=Path(self.test_dir) / "other.json")
        added = other.import_history(export_path)
        self.assertEqual(added, 2)
        self.assertEqual(len(other.load_all_quotes()), 2)

        # Re-importar no duplica (mismo quote_id)
        added_again = other.import_history(export_path)
        self.assertEqual(added_again, 0)
        self.assertEqual(len(other.load_all_quotes()), 2)

    def test_export_csv(self):
        self.mgr.save_quote(self._quote("COT-2026-0001"))
        csv_path = Path(self.test_dir) / "historial.csv"
        self.mgr.export_history_csv(csv_path)
        content = csv_path.read_text(encoding="utf-8-sig")
        self.assertIn("COT-2026-0001", content)
        self.assertIn("Cliente", content)


if __name__ == "__main__":
    unittest.main()
