import sys
import os
import unittest
import json
from unittest.mock import patch, MagicMock
import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.ai_service import (
    check_ollama_status,
    extract_bom_with_ai,
    suggest_alternatives_with_ai,
    verify_matches_with_ai,
)
from src.core.bom_parser import parse_bom_text_hybrid
from src.config import AppConfig

class TestAIFeaturesMocked(unittest.TestCase):

    def test_check_ollama_status(self):
        """Validates checking Ollama status with mock responses."""
        with patch('httpx.Client.get') as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_get.return_value = mock_resp
            self.assertTrue(check_ollama_status("http://localhost:11434"))

            # Test failure
            mock_get.side_effect = httpx.ConnectError("Connection refused")
            self.assertFalse(check_ollama_status("http://localhost:11434"))

    def test_extract_bom_with_ai_success(self):
        """Validates successful extraction of BOM items from messy text using mocked Ollama response."""
        mock_ai_json = {
            "response": json.dumps({
                "items": [
                    {"cantidad": 3, "componente": "Arduino Uno R3 con cable USB"},
                    {"cantidad": 20, "componente": "Resistencia 1k ohm 0.5W"},
                    {"cantidad": 1, "componente": "Sensor DHT22"}
                ]
            })
        }

        with patch('httpx.Client.post') as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_ai_json
            mock_post.return_value = mock_resp

            raw_chat = "Hola necesito 3 arduinos uno con cable, unas 20 resistencias de un kilo a medio watt y 1 sensor dht22"
            result = extract_bom_with_ai(raw_chat, host="http://localhost:11434", model="qwen2.5:7b")

            self.assertIsNotNone(result)
            self.assertEqual(len(result), 3)
            self.assertEqual(result[0]["cantidad"], 3)
            self.assertEqual(result[0]["componente"], "Arduino Uno R3 con cable USB")
            self.assertEqual(result[1]["cantidad"], 20)
            self.assertEqual(result[2]["cantidad"], 1)

    def test_suggest_alternatives_with_ai_success(self):
        """Validates suggesting component alternatives using mocked Ollama response."""
        mock_alts_json = {
            "response": json.dumps({
                "alternativas": [
                    {
                        "nombre": "ESP32 DevKit V1",
                        "explicacion": "Mayor potencia y compatible con WiFi/Bluetooth",
                        "compatibilidad": "Directa (Pin a Pin)"
                    },
                    {
                        "nombre": "NodeMCU v3 CH340",
                        "explicacion": "Mismo ESP8266 con controlador USB CH340",
                        "compatibilidad": "100% compatible de código"
                    }
                ]
            })
        }

        with patch('httpx.Client.post') as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_alts_json
            mock_post.return_value = mock_resp

            alts = suggest_alternatives_with_ai("NodeMCU CP2102", reason="Agotado")
            self.assertEqual(len(alts), 2)
            self.assertEqual(alts[0]["nombre"], "ESP32 DevKit V1")
            self.assertEqual(alts[0]["compatibilidad"], "Directa (Pin a Pin)")
            self.assertEqual(alts[1]["nombre"], "NodeMCU v3 CH340")

    def test_verify_matches_with_ai_success(self):
        """Validates batch verification of BOM candidates with mocked Ollama response."""
        mock_json = {
            "response": json.dumps({
                "verificaciones": [
                    {"componente": "LED rojo 5 mm", "match": False, "precio_ok": False,
                     "razon": "El candidato es una fuente de poder, no un LED"},
                    {"componente": "Sensor DHT22", "match": True, "precio_ok": True, "razon": ""},
                ]
            })
        }

        with patch('httpx.Client.post') as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_json
            mock_post.return_value = mock_resp

            items = [
                {"componente": "LED rojo 5 mm", "candidato": "FE-305D Fuente 30Vcc", "tienda": "RyCH", "precio": 630.0},
                {"componente": "Sensor DHT22", "candidato": "Sensor DHT22", "tienda": "DIY", "precio": 59.0},
            ]
            result = verify_matches_with_ai(items, host="http://localhost:11434", model="qwen2.5:7b")

            self.assertIsNotNone(result)
            self.assertEqual(len(result), 2)
            self.assertFalse(result[0]["match"])
            self.assertFalse(result[0]["precio_ok"])
            self.assertIn("fuente", result[0]["razon"].lower())
            self.assertTrue(result[1]["match"])

    def test_verify_matches_with_ai_failure_returns_none(self):
        """Validates that verification failures return None (no crash)."""
        with patch('httpx.Client.post') as mock_post:
            mock_post.side_effect = httpx.ConnectError("Ollama is offline")
            result = verify_matches_with_ai([{"componente": "X", "candidato": "Y", "tienda": "Z", "precio": 1.0}])
            self.assertIsNone(result)

    def test_verify_matches_empty_input(self):
        self.assertIsNone(verify_matches_with_ai([]))

    def test_hybrid_bom_parser_fallback_when_ollama_down(self):
        """Validates that parse_bom_text_hybrid seamlessly falls back to regex when Ollama fails."""
        cfg = AppConfig(enable_ai=True, ollama_url="http://localhost:11434", ollama_model="qwen2.5:7b")

        # Simulate Ollama down / connection timeout
        with patch('httpx.Client.post') as mock_post:
            mock_post.side_effect = httpx.ConnectError("Ollama is offline")

            text = "2x ESP32 NodeMCU\n10x Resistencia 220 ohm"
            parse_res = parse_bom_text_hybrid(text, config=cfg, force_ai=True)

            self.assertEqual(parse_res.source, "regex")
            self.assertEqual(len(parse_res.items), 2)
            self.assertEqual(parse_res.items[0].quantity, 2)
            self.assertEqual(parse_res.items[0].product_query, "ESP32 NodeMCU")
            self.assertEqual(parse_res.items[1].quantity, 10)

if __name__ == "__main__":
    unittest.main()
