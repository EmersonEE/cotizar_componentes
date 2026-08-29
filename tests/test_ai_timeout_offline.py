import sys
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.bom_parser import (
    ParsedBOMItem,
    BOMParseResult,
    parse_bom_text,
    parse_bom_text_hybrid,
    is_suspicious_regex_fallback,
)
from src.config import AppConfig


def _long_conversational_text() -> str:
    # Párrafo único y largo con marcadores conversacionales (caso WhatsApp)
    return "Hola, necesito una cotización para un proyecto: " + "resistencia 220, led rojo, led azul, " * 10


class TestAITimeoutOffline(unittest.TestCase):

    def test_config_ai_timeout_default_and_load(self):
        self.assertEqual(AppConfig().ai_timeout, 90.0)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "config.json"
            p.write_text('{"ai_timeout": 45.0}', encoding="utf-8")
            self.assertEqual(AppConfig.load(p).ai_timeout, 45.0)

    @patch("src.core.bom_parser.extract_bom_with_ai")
    def test_hybrid_uses_config_timeout(self, mock_ai):
        mock_ai.return_value = [{"cantidad": 1, "componente": "ESP32"}]
        cfg = AppConfig()
        cfg.ai_timeout = 123.0
        parse_bom_text_hybrid("2x ESP32", config=cfg, force_ai=True)
        self.assertEqual(mock_ai.call_args.kwargs["timeout"], 123.0)

    @patch("src.core.bom_parser.extract_bom_with_ai")
    def test_hybrid_explicit_timeout_overrides(self, mock_ai):
        mock_ai.return_value = [{"cantidad": 1, "componente": "ESP32"}]
        parse_bom_text_hybrid("2x ESP32", config=AppConfig(), force_ai=True, timeout=7.0)
        self.assertEqual(mock_ai.call_args.kwargs["timeout"], 7.0)


class TestSuspiciousFallbackOffline(unittest.TestCase):

    def test_conversational_paragraph_is_suspicious(self):
        text = _long_conversational_text()
        res = parse_bom_text(text)  # 1 ítem con todo el texto
        self.assertEqual(res.total_items, 1)
        self.assertTrue(is_suspicious_regex_fallback(text, res))

    def test_per_line_list_is_not_suspicious(self):
        text = "2x ESP32\n10x Resistencia 220 ohm 1/4W\nPantalla OLED 0.96"
        res = parse_bom_text(text)
        self.assertGreaterEqual(res.total_items, 3)
        self.assertFalse(is_suspicious_regex_fallback(text, res))

    def test_three_or_more_items_never_suspicious(self):
        text = _long_conversational_text()
        items = [
            ParsedBOMItem(raw_line="1x A", quantity=1, product_query="A", is_valid=True),
            ParsedBOMItem(raw_line="1x B", quantity=1, product_query="B", is_valid=True),
            ParsedBOMItem(raw_line="1x C", quantity=1, product_query="C", is_valid=True),
        ]
        res = BOMParseResult(items=items, invalid_lines=[], source="regex")
        self.assertFalse(is_suspicious_regex_fallback(text, res))

    def test_ai_source_never_suspicious(self):
        res = BOMParseResult(items=[], invalid_lines=[], source="ai_ollama")
        self.assertFalse(is_suspicious_regex_fallback(_long_conversational_text(), res))

    def test_short_text_not_suspicious(self):
        res = parse_bom_text("Hola, necesito ESP32")
        self.assertFalse(is_suspicious_regex_fallback("Hola, necesito ESP32", res))


if __name__ == "__main__":
    unittest.main()
