import sys
import os
import json
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.config import AppConfig, _parse_bool
from src.models import Product, Customer, BusinessInfo
from src.core.calculator import QuoteCalculator
from src.core.exporter import QuoteExporter


class TestConfigValidationOffline(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.config_path = Path(self.test_dir) / "config.json"
        self.output_dir = Path(self.test_dir) / "output"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _write_config(self, data: dict):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def test_strict_enable_ai_parsing(self):
        """'enable_ai' en string ('false'/'off') debe parsear a False, no a True."""
        self._write_config({"enable_ai": "false"})
        self.assertFalse(AppConfig.load(self.config_path).enable_ai)

        self._write_config({"enable_ai": "off"})
        self.assertFalse(AppConfig.load(self.config_path).enable_ai)

        self._write_config({"enable_ai": "true"})
        self.assertTrue(AppConfig.load(self.config_path).enable_ai)

        self._write_config({"enable_ai": 0})
        self.assertFalse(AppConfig.load(self.config_path).enable_ai)

        self._write_config({})  # ausente -> default True
        self.assertTrue(AppConfig.load(self.config_path).enable_ai)

    def test_parse_bool_edge_cases(self):
        self.assertTrue(_parse_bool(True))
        self.assertFalse(_parse_bool(False))
        self.assertTrue(_parse_bool("yes"))
        self.assertFalse(_parse_bool("no"))
        self.assertTrue(_parse_bool("SI"))
        self.assertTrue(_parse_bool("1"))
        self.assertTrue(_parse_bool("on"))
        self.assertTrue(_parse_bool("valor-extraño", default=True))
        self.assertFalse(_parse_bool("valor-extraño", default=False))

    def test_negative_values_clamped(self):
        """Valores de negocio inválidos se ajustan sin romper la app."""
        self._write_config({
            "service_fee_percent": -5.0,
            "validity_days": 0,
            "shipping_rules": {
                "La Electrónica": {"free_threshold": -10.0, "default_cost": -3.0, "is_pickup_only": False},
                "Electrónica DIY": {"free_threshold": 250.0, "default_cost": 35.0, "is_pickup_only": False},
                "Electrónica RyCH": {"free_threshold": None, "default_cost": 0.0, "is_pickup_only": True},
            },
        })
        cfg = AppConfig.load(self.config_path)
        self.assertEqual(cfg.service_fee_percent, 0.0)
        self.assertEqual(cfg.validity_days, 1)
        self.assertEqual(cfg.shipping_rules["La Electrónica"]["free_threshold"], 0.0)
        self.assertEqual(cfg.shipping_rules["La Electrónica"]["default_cost"], 0.0)

    def test_shipping_rules_fallback_to_defaults(self):
        """Sin sección shipping_rules en config.json se usan los defaults unificados."""
        self._write_config({"service_fee_percent": 10.0})
        cfg = AppConfig.load(self.config_path)
        self.assertIn("Electrónica RyCH", cfg.shipping_rules)
        self.assertTrue(cfg.shipping_rules["Electrónica RyCH"]["is_pickup_only"])
        self.assertIsNone(cfg.shipping_rules["Electrónica RyCH"]["free_threshold"])


class TestQuoteValidityAndTemplateOffline(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.output_dir = Path(self.test_dir) / "output"
        self.exporter = QuoteExporter(output_dir=self.output_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _build_quote(self, validity_days: int = 5):
        prod = Product(name="ESP32", url="https://example.com/esp32", store_name="La Electrónica", unit_price=90.0)
        item = QuoteCalculator.create_quote_item(prod, 2)
        return QuoteCalculator.build_quote(
            quote_id="COT-2026-0001",
            items=[item],
            customer=Customer(name="Cliente Test"),
            shipping_details=[],
            service_fee_percent=10.0,
            validity_days=validity_days
        )

    def test_validity_days_roundtrip(self):
        """validity_days se persiste en to_dict/from_dict y respeta legacy."""
        q = self._build_quote(validity_days=10)
        self.assertEqual(q.validity_days, 10)

        restored = type(q).from_dict(q.to_dict())
        self.assertEqual(restored.validity_days, 10)

        legacy = {"quote_id": "COT-2026-0001", "customer": {}, "items": [], "shipping_details": [],
                  "subtotal": 0.0, "total": 0.0}
        legacy_q = type(q).from_dict(legacy)
        self.assertEqual(legacy_q.validity_days, 5)

    def test_template_renders_validity_days(self):
        """La plantilla usa quote.validity_days en vez del texto fijo '5 días'."""
        q = self._build_quote(validity_days=10)
        biz = BusinessInfo(name="Emerson Electrónica", phone="4996-4191")
        html = self.exporter.render_html_string(q, biz, is_internal=False)
        self.assertIn("10 días", html)
        self.assertNotIn("válida por <strong>5 días</strong>", html)

    def test_template_renders_logo_and_payment_terms(self):
        """logo_url y payment_terms de la config se renderizan en el documento."""
        q = self._build_quote()
        biz = BusinessInfo(
            name="Emerson Electrónica",
            phone="4996-4191",
            logo_url="https://example.com/logo.png",
            payment_terms="Transferencia bancaria o efectivo contra entrega.",
        )
        html = self.exporter.render_html_string(q, biz, is_internal=False)
        self.assertIn('src="https://example.com/logo.png"', html)
        self.assertIn("Transferencia bancaria o efectivo contra entrega.", html)


if __name__ == "__main__":
    unittest.main()
