import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.models import Customer
from src.config import AppConfig
from src.core.bom_parser import ParsedBOMItem
from src.scrapers.search import SearchResultItem
from src.core.bom_searcher import (
    calculate_match_score,
    search_single_bom_item,
    find_optimal_mixed_assignment,
    build_all_bom_scenarios,
    MatchResult
)

class TestBOMEnhancedOptimizerOffline(unittest.TestCase):

    def setUp(self):
        self.config = AppConfig()
        self.config.service_fee_percent = 10.0
        self.config.shipping_rules = {
            "Electrónica RyCH": {"is_pickup_only": True, "free_threshold": None, "default_cost": 0.0},
            "La Electrónica": {"is_pickup_only": False, "free_threshold": 150.0, "default_cost": 35.0},
            "Electrónica DIY": {"is_pickup_only": False, "free_threshold": 250.0, "default_cost": 35.0}
        }
        self.customer = Customer(name="Cliente BOM Test")

    def test_score_and_confidence_levels(self):
        """Validates score calculation and confidence classifications."""
        # High confidence (exact keyword and numbers)
        score_high = calculate_match_score("ESP32 NodeMCU", "Modulo ESP32 NodeMCU WiFi Bluetooth", in_stock=True)
        self.assertGreaterEqual(score_high, 0.70)

        # Medium confidence (partial overlap)
        score_med = calculate_match_score("Pantalla OLED 0.96 I2C", "Pantalla Display LCD 0.96", in_stock=True)
        self.assertTrue(0.30 <= score_med <= 0.85)

        # Review / Penalty for wrong number (e.g. 220 ohm vs 10k ohm)
        score_wrong_num = calculate_match_score("Resistencia 220 ohm", "Resistencia 10k ohm", in_stock=True)
        self.assertLess(score_wrong_num, 0.50)

    @patch('src.core.bom_searcher.metasearch')
    def test_rejects_zero_or_negative_price_candidates(self, mock_meta):
        """Validates that candidate items with price <= 0 or missing are rejected."""
        mock_meta.return_value = [
            SearchResultItem(store_name="La Electrónica", title="ESP32 Gratis", url="https://example.com/free", unit_price=0.0, in_stock=True, stock_status="Disponible"),
            SearchResultItem(store_name="La Electrónica", title="ESP32 Negativo", url="https://example.com/neg", unit_price=-10.0, in_stock=True, stock_status="Disponible"),
            SearchResultItem(store_name="La Electrónica", title="ESP32 Válido", url="https://example.com/valid", unit_price=85.0, in_stock=True, stock_status="Disponible"),
        ]

        bom_item = ParsedBOMItem(raw_line="1x ESP32", quantity=1, product_query="ESP32", is_valid=True)
        result = search_single_bom_item(bom_item)

        # Should only have the valid candidate
        self.assertEqual(len(result.all_candidates), 1)
        self.assertEqual(result.all_candidates[0][0].unit_price, 85.0)
        self.assertEqual(result.best_match.title, "ESP32 Válido")

    @patch('src.core.bom_searcher.metasearch')
    def test_exact_diy_variant_url_preservation(self, mock_meta):
        """Validates that Shopify variant query parameters are preserved intact."""
        variant_url = "https://electronicadiy.com/products/esp32-devkit?variant=40123456789"
        mock_meta.return_value = [
            SearchResultItem(store_name="Electrónica DIY", title="ESP32 DevKit V1", url=variant_url, unit_price=95.0, in_stock=True, stock_status="Disponible")
        ]

        bom_item = ParsedBOMItem("1x ESP32 DevKit", 1, "ESP32 DevKit", True)
        result = search_single_bom_item(bom_item)

        self.assertIsNotNone(result.best_match)
        self.assertEqual(result.best_match.url, variant_url)

    @patch('src.core.bom_searcher.metasearch')
    def test_review_status_and_manual_confirmation(self, mock_meta):
        """Validates that low/medium score items get REVISAR status and can be confirmed."""
        mock_meta.return_value = [
            SearchResultItem(store_name="La Electrónica", title="Sensor Ultra Especial", url="https://example.com/sensor", unit_price=30.0, in_stock=True, stock_status="Disponible")
        ]

        bom_item = ParsedBOMItem("1x Sensor Ultra Raro", 1, "Sensor Ultra Raro", True)
        result = search_single_bom_item(bom_item)

        if result.status == "REVISAR":
            self.assertTrue(result.requires_review_confirmation)
            # Manual confirmation
            result.is_confirmed = True
            self.assertFalse(result.requires_review_confirmation)

    def test_preserves_original_bom_line_order(self):
        """Validates that generated quote items follow the exact order of the original BOM lines."""
        cand_a = SearchResultItem(store_name="Electrónica RyCH", title="Componente A", url="https://example.com/a", unit_price=10.0, in_stock=True, stock_status="Disponible")
        cand_b = SearchResultItem(store_name="Electrónica RyCH", title="Componente B", url="https://example.com/b", unit_price=20.0, in_stock=True, stock_status="Disponible")
        cand_c = SearchResultItem(store_name="Electrónica RyCH", title="Componente C", url="https://example.com/c", unit_price=30.0, in_stock=True, stock_status="Disponible")

        m1 = MatchResult(ParsedBOMItem("1", 1, "A", True), cand_a, [(cand_a, 0.9)], 0.9, "ALTA", cand_a, True)
        m2 = MatchResult(ParsedBOMItem("2", 2, "B", True), cand_b, [(cand_b, 0.9)], 0.9, "ALTA", cand_b, True)
        m3 = MatchResult(ParsedBOMItem("3", 3, "C", True), cand_c, [(cand_c, 0.9)], 0.9, "ALTA", cand_c, True)

        scenarios = build_all_bom_scenarios([m1, m2, m3], self.customer, self.config, 10.0)
        mixed_items = scenarios[0].items

        self.assertEqual(len(mixed_items), 3)
        self.assertEqual(mixed_items[0].product.name, "Componente A")
        self.assertEqual(mixed_items[1].product.name, "Componente B")
        self.assertEqual(mixed_items[2].product.name, "Componente C")

    def test_optimal_mixed_scenario_minimizes_total_cost(self):
        """
        Validates that the mixed scenario optimizer finds the global minimum:
        componentes + envíos + servicio (rather than simple greedy component cost).
        
        Scenario test:
        Item 1 (qty 1):
          - La Electrónica: Q 50.00
          - Electrónica RyCH: Q 58.00
        Item 2 (qty 1):
          - La Electrónica: Q 110.00
          - Electrónica RyCH: Q 125.00

        Analysis:
        - If we buy both from La Electrónica:
          Subtotal = 50 + 110 = 160.00 >= 150.00 (Free shipping!)
          Service fee (10%) = Q 16.00
          Shipping = Q 0.00
          Grand Total = 160.00 + 16.00 + 0 = Q 176.00

        - If we split or buy only in RyCH:
          RyCH subtotal = 58 + 125 = 183.00, fee = 18.30, shipping = 0 -> Total = Q 201.30.
        
        Optimizer must choose La Electrónica for both to reach free shipping threshold and minimize total!
        """
        cand_1_la = SearchResultItem(store_name="La Electrónica", title="Item 1", url="https://la.com/1", unit_price=50.0, in_stock=True, stock_status="Disponible")
        cand_1_rych = SearchResultItem(store_name="Electrónica RyCH", title="Item 1", url="https://rych.com/1", unit_price=58.0, in_stock=True, stock_status="Disponible")

        cand_2_la = SearchResultItem(store_name="La Electrónica", title="Item 2", url="https://la.com/2", unit_price=110.0, in_stock=True, stock_status="Disponible")
        cand_2_rych = SearchResultItem(store_name="Electrónica RyCH", title="Item 2", url="https://rych.com/2", unit_price=125.0, in_stock=True, stock_status="Disponible")

        m1 = MatchResult(
            bom_item=ParsedBOMItem("1x Item 1", 1, "Item 1", True),
            best_match=cand_1_la,
            all_candidates=[(cand_1_la, 0.95), (cand_1_rych, 0.90)],
            confidence_score=0.95,
            status="ALTA"
        )
        m2 = MatchResult(
            bom_item=ParsedBOMItem("1x Item 2", 1, "Item 2", True),
            best_match=cand_2_la,
            all_candidates=[(cand_2_la, 0.95), (cand_2_rych, 0.90)],
            confidence_score=0.95,
            status="ALTA"
        )

        optimal_items, missing = find_optimal_mixed_assignment(
            match_results=[m1, m2],
            shipping_rules=self.config.shipping_rules,
            service_fee_percent=10.0
        )

        self.assertEqual(len(missing), 0)
        self.assertEqual(len(optimal_items), 2)
        # Both must be from La Electrónica to get free shipping
        self.assertEqual(optimal_items[0].product.store_name, "La Electrónica")
        self.assertEqual(optimal_items[1].product.store_name, "La Electrónica")
        self.assertEqual(optimal_items[0].subtotal + optimal_items[1].subtotal, 160.0)

    def test_optimal_mixed_scenario_prefers_pickup_when_shipping_would_cost_more(self):
        """
        Validates that when reaching shipping threshold is not possible,
        optimizer prefers pickup store (RyCH) if item price + fee + shipping in other store is higher.

        Item 1 (qty 1):
          - La Electrónica: Q 20.00 (Shipping Q 35.00 -> Total = 20 + 2 + 35 = Q 57.00)
          - Electrónica RyCH: Q 28.00 (Shipping Q 0.00 -> Total = 28 + 2.80 + 0 = Q 30.80)
        
        Optimizer must choose RyCH because Q 30.80 < Q 57.00.
        """
        cand_la = SearchResultItem(store_name="La Electrónica", title="Sensor", url="https://la.com/sensor", unit_price=20.0, in_stock=True, stock_status="Disponible")
        cand_rych = SearchResultItem(store_name="Electrónica RyCH", title="Sensor", url="https://rych.com/sensor", unit_price=28.0, in_stock=True, stock_status="Disponible")

        m = MatchResult(
            bom_item=ParsedBOMItem("1x Sensor", 1, "Sensor", True),
            best_match=cand_la,
            all_candidates=[(cand_la, 0.95), (cand_rych, 0.90)],
            confidence_score=0.95,
            status="ALTA"
        )

        optimal_items, missing = find_optimal_mixed_assignment(
            match_results=[m],
            shipping_rules=self.config.shipping_rules,
            service_fee_percent=10.0
        )

        self.assertEqual(len(optimal_items), 1)
        self.assertEqual(optimal_items[0].product.store_name, "Electrónica RyCH")
        self.assertEqual(optimal_items[0].unit_price, 28.0)

if __name__ == "__main__":
    unittest.main()
