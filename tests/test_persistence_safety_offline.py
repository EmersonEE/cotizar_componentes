import sys
import os
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.models import Product, Customer, QuoteStatus
from src.core.calculator import QuoteCalculator
from src.core.history_manager import HistoryManager


def _make_quote(quote_id: str, product_name: str, price: float, qty: int = 1, customer_name: str = "Cliente"):
    prod = Product(name=product_name, url="https://example.com/x", store_name="La Electrónica", unit_price=price)
    item = QuoteCalculator.create_quote_item(prod, qty)
    return QuoteCalculator.build_quote(
        quote_id=quote_id,
        items=[item],
        customer=Customer(name=customer_name),
        shipping_details=[],
        service_fee_percent=10.0
    )


class TestPersistenceSafetyOffline(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.history_file = Path(self.test_dir) / "history.json"
        self.mgr = HistoryManager(file_path=self.history_file)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_backup_created_on_second_write(self):
        """El backup .bak se crea al segundo guardado y contiene el estado anterior."""
        q1 = _make_quote("COT-2026-0001", "ESP32", 100.0)
        q2 = _make_quote("COT-2026-0002", "DHT22", 50.0)
        self.mgr.save_quote(q1)
        self.mgr.save_quote(q2)

        backup = self.history_file.with_suffix(".json.bak")
        self.assertTrue(backup.exists())
        with open(backup, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual([q["quote_id"] for q in data], ["COT-2026-0001"])

    def test_collision_reassigns_new_id(self):
        """Dos cotizaciones distintas con el mismo ID fresco: la segunda recibe ID nuevo."""
        q1 = _make_quote("COT-2026-0001", "ESP32", 100.0)
        q2 = _make_quote("COT-2026-0001", "Arduino UNO", 200.0)  # mismo ID, contenido distinto
        self.mgr.save_quote(q1)
        self.mgr.save_quote(q2)

        # q2 fue reasignado a un ID secuencial nuevo
        year = datetime.now().year
        self.assertNotEqual(q2.quote_id, "COT-2026-0001")
        self.assertTrue(q2.quote_id.startswith(f"COT-{year}-"))
        self.assertEqual(q2.quote_id, f"COT-{year}-0002")

        all_q = self.mgr.load_all_quotes()
        self.assertEqual(len(all_q), 2)
        self.assertEqual(all_q[0].quote_id, "COT-2026-0001")
        self.assertEqual(all_q[0].items[0].product.name, "ESP32")
        self.assertEqual(all_q[1].quote_id, q2.quote_id)
        self.assertEqual(all_q[1].items[0].product.name, "Arduino UNO")

    def test_identical_resave_is_idempotent(self):
        """Re-guardar el mismo contenido conserva el ID y no duplica."""
        q1 = _make_quote("COT-2026-0001", "ESP32", 100.0)
        self.mgr.save_quote(q1)
        q2 = _make_quote("COT-2026-0001", "ESP32", 100.0)  # mismo contenido (timestamps distintos)
        self.mgr.save_quote(q2)

        self.assertEqual(q2.quote_id, "COT-2026-0001")
        all_q = self.mgr.load_all_quotes()
        self.assertEqual(len(all_q), 1)

    def test_status_update_keeps_same_id(self):
        """update_quote_status sobrescribe deliberadamente sin duplicar."""
        q = _make_quote("COT-2026-0001", "Sensor", 30.0)
        self.mgr.save_quote(q)

        updated = self.mgr.update_quote_status("COT-2026-0001", QuoteStatus.ENVIADA)
        self.assertEqual(updated.quote_id, "COT-2026-0001")
        self.assertEqual(updated.status, QuoteStatus.ENVIADA.value)

        all_q = self.mgr.load_all_quotes()
        self.assertEqual(len(all_q), 1)
        self.assertEqual(all_q[0].status, QuoteStatus.ENVIADA.value)

    def test_corrupt_file_recovered_from_backup(self):
        """Un history.json corrupto se recupera desde el .bak y se repara."""
        q1 = _make_quote("COT-2026-0001", "ESP32", 100.0)
        q2 = _make_quote("COT-2026-0002", "DHT22", 50.0)
        q3 = _make_quote("COT-2026-0003", "Relay", 25.0)
        self.mgr.save_quote(q1)
        self.mgr.save_quote(q2)
        self.mgr.save_quote(q3)  # .bak ahora contiene [q1, q2]

        # Corromper el archivo principal
        with open(self.history_file, "w", encoding="utf-8") as f:
            f.write("{ esto no es json válido ")

        recovered = self.mgr.load_all_quotes()
        self.assertEqual(len(recovered), 2)
        ids = {q.quote_id for q in recovered}
        self.assertEqual(ids, {"COT-2026-0001", "COT-2026-0002"})

        # El archivo principal quedó reparado
        with open(self.history_file, "r", encoding="utf-8") as f:
            json.load(f)  # no debe lanzar

    def test_next_quote_id_skips_versioned_quotes(self):
        """La numeración ignora versiones _vN y usa el base_id."""
        q1 = _make_quote("COT-2026-0001", "A", 10.0)
        q2 = _make_quote("COT-2026-0001_v2", "A", 12.0)
        q3 = _make_quote("COT-2026-0002", "B", 20.0)
        self.mgr.save_quote(q1)
        self.mgr.save_quote(q2)
        self.mgr.save_quote(q3)

        year = datetime.now().year
        self.assertEqual(self.mgr.get_next_quote_id("COT"), f"COT-{year}-0003")


if __name__ == "__main__":
    unittest.main()
