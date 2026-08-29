import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.bom_parser import parse_bom_text, parse_bom_line
from src.core.bom_searcher import (
    calculate_match_score,
    search_bom_items_parallel,
    build_all_bom_scenarios,
)
from src.models import Customer
from src.config import AppConfig
from src.scrapers.search import SearchResultItem


class TestBOMParserOffline(unittest.TestCase):

    def test_bom_parser_formats(self):
        sample_text = """
        2x ESP32 NodeMCU
        10x Resistencia 220 ohm 1/4W
        Sensor de temperatura DHT22
        Modulo Relay 5V 2 canales
        Pantalla OLED 0.96 I2C
        5 pcs Arduino Uno R3
        - 4x LM358
        * 100 × Resistencia 10k 1/4W
        Protoboard 830 puntos
        LED Rojo 5mm (x50)
        // Línea de comentario
        # Otra nota
        """

        res = parse_bom_text(sample_text)
        self.assertEqual(res.total_items, 10)
        self.assertEqual(res.total_quantity, 2 + 10 + 1 + 1 + 1 + 5 + 4 + 100 + 1 + 50)

        expected = [
            (2, "ESP32 NodeMCU"),
            (10, "Resistencia 220 ohm 1/4W"),
            (1, "Sensor de temperatura DHT22"),
            (1, "Modulo Relay 5V 2 canales"),
            (1, "Pantalla OLED 0.96 I2C"),
            (5, "Arduino Uno R3"),
            (4, "LM358"),
            (100, "Resistencia 10k 1/4W"),
            (1, "Protoboard 830 puntos"),
            (50, "LED Rojo 5mm"),
        ]
        for item, (exp_qty, exp_name) in zip(res.items, expected):
            self.assertEqual(item.quantity, exp_qty)
            self.assertEqual(item.product_query, exp_name)

    def test_parse_bom_line_edge_cases(self):
        self.assertIsNone(parse_bom_line(""))
        self.assertIsNone(parse_bom_line("# comentario"))
        self.assertIsNone(parse_bom_line("// comentario"))
        item = parse_bom_line("5x ESP32")
        self.assertEqual(item.quantity, 5)
        self.assertEqual(item.product_query, "ESP32")


class TestMatchScoringOffline(unittest.TestCase):

    def test_positive_and_negative_matches(self):
        s1 = calculate_match_score("Sensor de temperatura DHT22", "MD-DHT22 Sensor de Temperatura y Humedad Digital", True)
        self.assertGreaterEqual(s1, 0.75)

        s2 = calculate_match_score("Sensor de temperatura DHT22", "MD-DHT11 Sensor de Temperatura y Humedad", True)
        self.assertLess(s2, 0.40)

    def test_resistor_values(self):
        s3 = calculate_match_score("Resistencia 220 ohm 1/4W", "Resistencia 220 Ohm a 1/4 W", True)
        s4 = calculate_match_score("Resistencia 220 ohm 1/4W", "Resistencia 10k Ohm a 1/4 W", True)
        self.assertGreater(s3, s4)


class TestBOMSearchAndScenariosOffline(unittest.TestCase):

    def _mock_metasearch(self):
        def fake_metasearch(query, max_per_store=5, timeout=6.0, global_timeout=30.0):
            q = query.lower()
            return [
                SearchResultItem(store_name="Electrónica RyCH", title=f"RYCH {query}",
                                 url=f"https://rych.com/{q}", unit_price=50.0, in_stock=True, stock_status="Disponible"),
                SearchResultItem(store_name="La Electrónica", title=f"LA {query}",
                                 url=f"https://la.com/{q}", unit_price=55.0, in_stock=True, stock_status="Disponible"),
                SearchResultItem(store_name="Electrónica DIY", title=f"DIY {query}",
                                 url=f"https://diy.com/{q}", unit_price=60.0, in_stock=True, stock_status="Disponible"),
            ]
        return fake_metasearch

    @patch("src.core.bom_searcher.metasearch")
    def test_parallel_search_returns_result_per_item(self, mock_meta):
        mock_meta.side_effect = self._mock_metasearch()
        bom_input = """
        2x ESP32 NodeMCU
        Sensor de temperatura DHT22
        Pantalla OLED 0.96 I2C
        """
        parse_res = parse_bom_text(bom_input)
        match_results = search_bom_items_parallel(parse_res.items, max_workers=5)
        self.assertEqual(len(match_results), len(parse_res.items))
        for m in match_results:
            self.assertIsNotNone(m.best_match)
            self.assertEqual(m.status, "ALTA")

    @patch("src.core.bom_searcher.metasearch")
    def test_four_scenarios_generation(self, mock_meta):
        mock_meta.side_effect = self._mock_metasearch()
        config = AppConfig()
        config.shipping_rules = {
            "Electrónica RyCH": {"is_pickup_only": True, "free_threshold": None, "default_cost": 0.0},
            "La Electrónica": {"is_pickup_only": False, "free_threshold": 150.0, "default_cost": 35.0},
            "Electrónica DIY": {"is_pickup_only": False, "free_threshold": 250.0, "default_cost": 35.0},
        }
        customer = Customer(name="Cliente Test")

        bom_input = """
        2x ESP32 NodeMCU
        10x Resistencia 220 ohm 1/4W
        """
        parse_res = parse_bom_text(bom_input)
        match_results = search_bom_items_parallel(parse_res.items, max_workers=5)
        scenarios = build_all_bom_scenarios(match_results, customer, config, service_fee_percent=10.0)

        self.assertEqual(len(scenarios), 4)
        # El orden de escenarios sigue al registro central de tiendas (src/stores.py)
        expected_titles = [
            "Cotización Mixta (Mejor Precio Combinado)",
            "Todo en La Electrónica",
            "Todo en Electrónica DIY",
            "Todo en Electrónica RyCH",
        ]
        for idx, (sc, exp_title) in enumerate(zip(scenarios, expected_titles), 1):
            self.assertEqual(sc.title, exp_title)
            self.assertEqual(sc.scenario_id, idx)
            self.assertTrue(len(sc.items) >= 2)


if __name__ == "__main__":
    unittest.main()
