import sys
import os
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.models import Product, Quote, Customer, BusinessInfo, StoreShippingDetail
from src.core.calculator import QuoteCalculator
from src.core.history_manager import HistoryManager
from src.core.exporter import QuoteExporter

class TestCustomerAndHistoryOffline(unittest.TestCase):

    def setUp(self):
        # Create isolated temporary directory for test history and exports
        self.test_dir = tempfile.mkdtemp()
        self.history_file = Path(self.test_dir) / "test_history.json"
        self.output_dir = Path(self.test_dir) / "output"
        self.history_mgr = HistoryManager(file_path=self.history_file)
        self.exporter = QuoteExporter(output_dir=self.output_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_customer_model_and_validation(self):
        """Validates Customer dataclass, defaults, validation and serialization."""
        # 1. Defaults
        c_default = Customer()
        self.assertEqual(c_default.name, "Cliente General")
        self.assertEqual(c_default.phone, "")
        self.assertEqual(c_default.email, "")
        self.assertEqual(c_default.notes, "")
        self.assertEqual(c_default.validate(), [])

        # 2. Complete data & valid email
        c_full = Customer(
            name="Ing. Carlos Mendoza",
            phone="+502 4433-2211",
            email="carlos.mendoza@empresa.gt",
            notes="Entrega en Zona 10 antes del mediodía."
        )
        self.assertEqual(c_full.validate(), [])
        
        # 3. Invalid email validation
        c_invalid_email = Customer(name="Ana", email="correo-sin-arroba.com")
        errors = c_invalid_email.validate()
        self.assertEqual(len(errors), 1)
        self.assertIn("no tiene un formato válido", errors[0])

        # 4. Serialization roundtrip
        c_dict = c_full.to_dict()
        c_restored = Customer.from_dict(c_dict)
        self.assertEqual(c_full, c_restored)

        # 5. Legacy compatibility: from_dict with missing email and notes
        legacy_data = {"name": "Cliente Antiguo", "phone": "12345678"}
        c_legacy = Customer.from_dict(legacy_data)
        self.assertEqual(c_legacy.name, "Cliente Antiguo")
        self.assertEqual(c_legacy.phone, "12345678")
        self.assertEqual(c_legacy.email, "")
        self.assertEqual(c_legacy.notes, "")

        # 6. Malformed input
        c_bad = Customer.from_dict(None)
        self.assertEqual(c_bad.name, "Cliente General")

    def test_quote_serialization_and_legacy_support(self):
        """Validates Quote serialization and backwards compatibility with legacy keys."""
        prod = Product(name="ESP32 NodeMCU", url="https://example.com/esp32", store_name="Electrónica DIY", unit_price=90.0)
        item = QuoteCalculator.create_quote_item(prod, 2) # 180.0
        cust = Customer(name="Lucía Gómez", phone="5555-1111", email="lucia@test.com", notes="Cliente recurrente")
        
        quote = QuoteCalculator.build_quote(
            quote_id="COT-2026-0001",
            items=[item],
            customer=cust,
            shipping_details=[StoreShippingDetail("Electrónica DIY", 180.0, 250.0, False, 35.0, "Q 35.00", False)],
            service_fee_percent=10.0
        )

        # Roundtrip
        q_dict = quote.to_dict()
        q_restored = Quote.from_dict(q_dict)
        self.assertEqual(quote.quote_id, q_restored.quote_id)
        self.assertEqual(quote.customer.email, q_restored.customer.email)
        self.assertEqual(quote.customer.notes, q_restored.customer.notes)
        self.assertEqual(quote.total, q_restored.total)

        # Legacy data without items_subtotal (only 'subtotal')
        legacy_quote_dict = {
            "quote_id": "COT-2025-0099",
            "date": "10/01/2025",
            "valid_until": "15/01/2025",
            "customer": {"name": "Cliente V1", "phone": "9999-8888"},
            "items": [],
            "subtotal": 500.0,
            "total": 550.0
        }
        legacy_quote = Quote.from_dict(legacy_quote_dict)
        self.assertEqual(legacy_quote.items_subtotal, 500.0)
        self.assertEqual(legacy_quote.subtotal, 500.0)
        self.assertEqual(legacy_quote.customer.email, "")
        self.assertEqual(legacy_quote.customer.notes, "")

    def test_search_quotes_multicriteria(self):
        """Validates searching quotes across ID, customer name, phone, email, notes, and date."""
        p = Product("Sensor DHT22", "https://example.com/dht22", "La Electrónica", 50.0)
        item = QuoteCalculator.create_quote_item(p, 1)

        q1 = QuoteCalculator.build_quote(
            quote_id="COT-2026-0001",
            items=[item],
            customer=Customer(name="Roberto Pérez", phone="4411-2233", email="roberto@domotica.gt", notes="Proyecto Domótica"),
            shipping_details=[]
        )
        q1.date = "25/08/2026"

        q2 = QuoteCalculator.build_quote(
            quote_id="COT-2026-0002",
            items=[item],
            customer=Customer(name="María Morales", phone="5588-9900", email="maria@solar.com", notes="Inversor Solar"),
            shipping_details=[]
        )
        q2.date = "27/08/2026"

        self.history_mgr.save_quote(q1)
        self.history_mgr.save_quote(q2)

        # 1. Search by ID
        res_id = self.history_mgr.search_quotes("COT-2026-0001")
        self.assertEqual(len(res_id), 1)
        self.assertEqual(res_id[0].quote_id, "COT-2026-0001")

        # 2. Search by Name (case-insensitive substring)
        res_name = self.history_mgr.search_quotes("morales")
        self.assertEqual(len(res_name), 1)
        self.assertEqual(res_name[0].customer.name, "María Morales")

        # 3. Search by Phone
        res_phone = self.history_mgr.search_quotes("4411")
        self.assertEqual(len(res_phone), 1)
        self.assertEqual(res_phone[0].quote_id, "COT-2026-0001")

        # 4. Search by Email
        res_email = self.history_mgr.search_quotes("solar.com")
        self.assertEqual(len(res_email), 1)
        self.assertEqual(res_email[0].quote_id, "COT-2026-0002")

        # 5. Search by Notes
        res_notes = self.history_mgr.search_quotes("domótica")
        self.assertEqual(len(res_notes), 1)
        self.assertEqual(res_notes[0].customer.name, "Roberto Pérez")

        # 6. Search by Date
        res_date = self.history_mgr.search_quotes("27/08/2026")
        self.assertEqual(len(res_date), 1)
        self.assertEqual(res_date[0].quote_id, "COT-2026-0002")

        # 7. Empty search returns all
        self.assertEqual(len(self.history_mgr.search_quotes("")), 2)

        # 8. No match
        self.assertEqual(len(self.history_mgr.search_quotes("inexistente")), 0)

    def test_duplicate_quote_independent(self):
        """Validates that duplicating a quote creates an independent quote without modifying the original."""
        p1 = Product("ESP32", "https://example.com/esp32", "Electrónica DIY", 100.0)
        p2 = Product("Relay 5V", "https://example.com/relay", "Electrónica RyCH", 25.0)
        items = [
            QuoteCalculator.create_quote_item(p1, 2), # 200.0
            QuoteCalculator.create_quote_item(p2, 4)  # 100.0
        ]
        
        orig_cust = Customer(name="Cliente Original", phone="1111-2222", email="orig@test.com", notes="Cotización Base")
        shipping = [
            StoreShippingDetail("Electrónica DIY", 200.0, 250.0, False, 35.0, "Q 35.00", False, True),
            StoreShippingDetail("Electrónica RyCH", 100.0, None, True, 0.0, "No aplica", True)
        ]
        
        original_quote = QuoteCalculator.build_quote(
            quote_id="COT-2026-0001",
            items=items,
            customer=orig_cust,
            shipping_details=shipping,
            service_fee_percent=10.0,
            version=1
        )
        self.history_mgr.save_quote(original_quote)

        # 1. Duplicate keeping same customer
        dup1 = self.history_mgr.duplicate_quote("COT-2026-0001")

        self.assertEqual(dup1.quote_id, "COT-2026-0002")
        self.assertEqual(dup1.version, 1)
        self.assertIsNone(dup1.base_quote_id)
        self.assertEqual(len(dup1.items), 2)
        self.assertEqual(dup1.items_subtotal, 300.0)
        self.assertEqual(dup1.service_fee_amount, 30.0)
        self.assertEqual(dup1.total_shipping, 35.0)
        self.assertEqual(dup1.total, 365.0)
        self.assertEqual(dup1.customer.name, "Cliente Original")

        # 2. Check original is completely untouched
        refreshed_orig = self.history_mgr.get_quote("COT-2026-0001")
        self.assertIsNotNone(refreshed_orig)
        self.assertEqual(refreshed_orig.quote_id, "COT-2026-0001")
        self.assertEqual(refreshed_orig.version, 1)
        self.assertEqual(refreshed_orig.total, 365.0)

        # 3. Duplicate assigning a new customer
        new_cust = Customer(name="Nuevo Cliente B", phone="3333-4444", email="nuevo@cliente.gt", notes="Duplicado para proyecto B")
        dup2 = self.history_mgr.duplicate_quote("COT-2026-0001", new_customer=new_cust)

        self.assertEqual(dup2.quote_id, "COT-2026-0003")
        self.assertEqual(dup2.customer.name, "Nuevo Cliente B")
        self.assertEqual(dup2.customer.email, "nuevo@cliente.gt")
        self.assertEqual(dup2.customer.notes, "Duplicado para proyecto B")

        # Total quotes in history must be 3
        all_quotes = self.history_mgr.load_all_quotes()
        self.assertEqual(len(all_quotes), 3)

    def test_exporter_with_email_and_notes(self):
        """Validates that customer email and notes are rendered in CSV, Client HTML, and Internal HTML."""
        p = Product("Pantalla OLED", "https://example.com/oled", "La Electrónica", 65.0)
        item = QuoteCalculator.create_quote_item(p, 1)
        cust = Customer(
            name="Ing. Sofía Morales",
            phone="+502 5544-3322",
            email="sofia.morales@iot.gt",
            notes="Requiere entrega en caja sellada con factura."
        )
        
        quote = QuoteCalculator.build_quote(
            quote_id="COT-2026-0001",
            items=[item],
            customer=cust,
            shipping_details=[StoreShippingDetail("La Electrónica", 65.0, 150.0, False, 35.0, "Q 35.00", False)],
            service_fee_percent=12.0
        )

        biz = BusinessInfo(name="Emerson Electrónica & Integración", phone="4996-4191", email="ventas@emerson.gt")

        # 1. Test CSV export
        csv_path = self.exporter.export_csv(quote)
        self.assertTrue(csv_path.exists())
        csv_content = csv_path.read_text(encoding="utf-8-sig")
        self.assertIn("sofia.morales@iot.gt", csv_content)
        self.assertIn("Requiere entrega en caja sellada con factura.", csv_content)

        # 2. Test Client HTML export
        client_html = self.exporter.render_html_string(quote, biz, is_internal=False)
        self.assertIn("sofia.morales@iot.gt", client_html)
        self.assertIn("Requiere entrega en caja sellada con factura.", client_html)
        self.assertNotIn("🔒 Control Interno", client_html)

        # 3. Test Internal HTML export
        internal_html = self.exporter.render_html_string(quote, biz, is_internal=True)
        self.assertIn("sofia.morales@iot.gt", internal_html)
        self.assertIn("Notas Internas:", internal_html)
        self.assertIn("Requiere entrega en caja sellada con factura.", internal_html)
        self.assertIn("🔒 Control Interno", internal_html)

if __name__ == "__main__":
    unittest.main()
