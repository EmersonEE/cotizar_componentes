import sys
import os
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.models import (
    Product, Quote, Customer, BusinessInfo, QuoteStatus, InvalidStatusTransitionError
)
from src.core.calculator import QuoteCalculator
from src.core.history_manager import HistoryManager
from src.core.exporter import QuoteExporter

class TestCommercialStatusesOffline(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.history_file = Path(self.test_dir) / "test_history.json"
        self.output_dir = Path(self.test_dir) / "output"
        self.history_mgr = HistoryManager(file_path=self.history_file)
        self.exporter = QuoteExporter(output_dir=self.output_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_legacy_quote_without_status_defaults_to_guardada(self):
        """Validates backward compatibility: quotes without 'status' deserialized as GUARDADA."""
        legacy_data = {
            "quote_id": "COT-2025-0010",
            "version": 1,
            "date": "15/05/2025",
            "valid_until": "20/05/2025",
            "customer": {"name": "Cliente Antiguo", "phone": "1234-5678"},
            "items": [],
            "subtotal": 100.0,
            "total": 112.0
        }
        quote = Quote.from_dict(legacy_data)
        self.assertEqual(quote.status, QuoteStatus.GUARDADA.value)
        self.assertIsNotNone(quote.status_updated_at)

    def test_valid_status_transitions(self):
        """Validates all permitted transitions along the commercial lifecycle."""
        p = Product("Sensor Ultrasonico", "https://example.com/sensor", "La Electrónica", 25.0)
        item = QuoteCalculator.create_quote_item(p, 2)
        
        quote = QuoteCalculator.build_quote(
            quote_id="COT-2026-0001",
            items=[item],
            customer=Customer(name="Cliente Prueba"),
            shipping_details=[]
        )
        quote.status = QuoteStatus.BORRADOR.value
        initial_time = quote.status_updated_at

        # 1. BORRADOR -> GUARDADA
        quote.change_status(QuoteStatus.GUARDADA)
        self.assertEqual(quote.status, QuoteStatus.GUARDADA.value)
        self.assertGreaterEqual(quote.status_updated_at, initial_time)

        # 2. GUARDADA -> ENVIADA
        quote.change_status("ENVIADA")
        self.assertEqual(quote.status, QuoteStatus.ENVIADA.value)

        # 3. ENVIADA -> ACEPTADA
        quote.change_status(QuoteStatus.ACEPTADA)
        self.assertEqual(quote.status, QuoteStatus.ACEPTADA.value)

        # 4. ACEPTADA -> GUARDADA (reapertura)
        quote.change_status(QuoteStatus.GUARDADA)
        self.assertEqual(quote.status, QuoteStatus.GUARDADA.value)

        # 5. GUARDADA -> RECHAZADA
        quote.change_status(QuoteStatus.RECHAZADA)
        self.assertEqual(quote.status, QuoteStatus.RECHAZADA.value)

        # 6. RECHAZADA -> GUARDADA (renegociación)
        quote.change_status(QuoteStatus.GUARDADA)
        self.assertEqual(quote.status, QuoteStatus.GUARDADA.value)

        # 7. GUARDADA -> VENCIDA
        quote.change_status(QuoteStatus.VENCIDA)
        self.assertEqual(quote.status, QuoteStatus.VENCIDA.value)

        # 8. VENCIDA -> GUARDADA (renovación)
        quote.change_status(QuoteStatus.GUARDADA)
        self.assertEqual(quote.status, QuoteStatus.GUARDADA.value)

    def test_invalid_status_transitions_raise_error(self):
        """Validates that unauthorized transitions raise InvalidStatusTransitionError."""
        p = Product("ESP32", "https://example.com/esp32", "Electrónica DIY", 85.0)
        item = QuoteCalculator.create_quote_item(p, 1)
        
        # 1. BORRADOR cannot jump directly to ACEPTADA or VENCIDA
        q_draft = QuoteCalculator.build_quote("COT-2026-0002", [item], Customer("Cliente"))
        q_draft.status = QuoteStatus.BORRADOR.value

        with self.assertRaises(InvalidStatusTransitionError):
            q_draft.change_status(QuoteStatus.ACEPTADA)

        with self.assertRaises(InvalidStatusTransitionError):
            q_draft.change_status(QuoteStatus.VENCIDA)

        # 2. ACEPTADA cannot transition directly to BORRADOR or VENCIDA
        q_accepted = QuoteCalculator.build_quote("COT-2026-0003", [item], Customer("Cliente"))
        q_accepted.status = QuoteStatus.ACEPTADA.value

        with self.assertRaises(InvalidStatusTransitionError):
            q_accepted.change_status(QuoteStatus.BORRADOR)

        with self.assertRaises(InvalidStatusTransitionError):
            q_accepted.change_status(QuoteStatus.VENCIDA)

        # 3. Invalid status string raises error
        with self.assertRaises(InvalidStatusTransitionError):
            q_accepted.change_status("ESTADO_INEXISTENTE")

    def test_history_persistence_and_update_status(self):
        """Validates that update_quote_status persists changes to history.json and updates timestamps."""
        p = Product("Modulo GPS", "https://example.com/gps", "La Electrónica", 120.0)
        item = QuoteCalculator.create_quote_item(p, 1)
        quote = QuoteCalculator.build_quote("COT-2026-0004", [item], Customer("Ing. Mario"))
        self.history_mgr.save_quote(quote)

        # Initial check
        loaded_1 = self.history_mgr.get_quote("COT-2026-0004")
        self.assertEqual(loaded_1.status, QuoteStatus.GUARDADA.value)

        # Update status to ENVIADA
        updated = self.history_mgr.update_quote_status("COT-2026-0004", QuoteStatus.ENVIADA)
        self.assertEqual(updated.status, QuoteStatus.ENVIADA.value)

        # Reload directly from storage
        loaded_2 = self.history_mgr.get_quote("COT-2026-0004")
        self.assertEqual(loaded_2.status, QuoteStatus.ENVIADA.value)
        self.assertEqual(loaded_2.status_updated_at, updated.status_updated_at)

        # Update to ACEPTADA
        self.history_mgr.update_quote_status("COT-2026-0004", "ACEPTADA")
        loaded_3 = self.history_mgr.get_quote("COT-2026-0004")
        self.assertEqual(loaded_3.status, QuoteStatus.ACEPTADA.value)

    def test_search_and_filter_by_status(self):
        """Validates searching and filtering quotes by commercial status."""
        p = Product("Resistencia", "https://example.com/res", "RyCH", 1.0)
        item = QuoteCalculator.create_quote_item(p, 10)

        q1 = QuoteCalculator.build_quote("COT-2026-0001", [item], Customer("Cliente 1"))
        q1.status = QuoteStatus.GUARDADA.value

        q2 = QuoteCalculator.build_quote("COT-2026-0002", [item], Customer("Cliente 2"))
        q2.status = QuoteStatus.ENVIADA.value

        q3 = QuoteCalculator.build_quote("COT-2026-0003", [item], Customer("Cliente 3"))
        q3.status = QuoteStatus.ACEPTADA.value

        q4 = QuoteCalculator.build_quote("COT-2026-0004", [item], Customer("Cliente 4"))
        q4.status = QuoteStatus.RECHAZADA.value

        for q in [q1, q2, q3, q4]:
            self.history_mgr.save_quote(q)

        # 1. Filter by ACEPTADA
        res_aceptadas = self.history_mgr.search_quotes(status_filter="ACEPTADA")
        self.assertEqual(len(res_aceptadas), 1)
        self.assertEqual(res_aceptadas[0].quote_id, "COT-2026-0003")

        # 2. Filter by ENVIADA
        res_enviadas = self.history_mgr.search_quotes(status_filter="ENVIADA")
        self.assertEqual(len(res_enviadas), 1)
        self.assertEqual(res_enviadas[0].quote_id, "COT-2026-0002")

        # 3. Filter by TODOS
        res_todos = self.history_mgr.search_quotes(status_filter="TODOS")
        self.assertEqual(len(res_todos), 4)

        # 4. Combined search query + status filter
        res_combined = self.history_mgr.search_quotes(query="Cliente 4", status_filter="RECHAZADA")
        self.assertEqual(len(res_combined), 1)
        self.assertEqual(res_combined[0].quote_id, "COT-2026-0004")

        # 5. Combined search query mismatch
        res_none = self.history_mgr.search_quotes(query="Cliente 1", status_filter="ACEPTADA")
        self.assertEqual(len(res_none), 0)

    def test_exporter_internal_vs_client_status_privacy(self):
        """
        Validates that commercial status is shown in internal documents and CSV,
        but NEVER exposed in the client-facing PDF/HTML document.
        """
        p = Product("Pantalla OLED", "https://example.com/oled", "La Electrónica", 65.0)
        item = QuoteCalculator.create_quote_item(p, 1)
        cust = Customer(name="Cliente VIP", phone="4433-2211", email="vip@cliente.com")
        
        quote = QuoteCalculator.build_quote("COT-2026-0005", [item], cust)
        quote.status = QuoteStatus.ACEPTADA.value

        biz = BusinessInfo(name="Emerson Electrónica")

        # 1. Client HTML should NOT expose internal commercial status badge in body
        client_html = self.exporter.render_html_string(quote, biz, is_internal=False)
        self.assertNotIn('<span class="status-badge', client_html)
        self.assertNotIn("🟢 ACEPTADA", client_html)

        # 2. Internal HTML MUST render the commercial status badge in body
        internal_html = self.exporter.render_html_string(quote, biz, is_internal=True)
        self.assertIn('<span class="status-badge', internal_html)
        self.assertIn("status-aceptada", internal_html)
        self.assertIn("🟢 ACEPTADA", internal_html)

        # 3. CSV must include ESTADO and status value
        csv_path = self.exporter.export_csv(quote)
        csv_content = csv_path.read_text(encoding="utf-8-sig")
        self.assertIn("ESTADO", csv_content)
        self.assertIn("ACEPTADA", csv_content)

if __name__ == "__main__":
    unittest.main()
