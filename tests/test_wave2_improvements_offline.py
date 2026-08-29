import sys
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.models import Product, Customer
from src.core.calculator import QuoteCalculator
from src.core.history_manager import HistoryManager
from src.core.bom_searcher import (
    MatchResult,
    ParsedBOMItem,
    _greedy_mixed_assignment,
    find_optimal_mixed_assignment,
)
from src.scrapers.search import SearchResultItem, _extract_price


class TestPriceExtractionT7(unittest.TestCase):
    """T7: los precios con separadores de miles ya no se rompen en la búsqueda."""

    def test_thousands_separators(self):
        self.assertEqual(_extract_price("Q 1,250.00"), 1250.0)
        self.assertEqual(_extract_price("1.250,00 Q"), 1250.0)
        self.assertEqual(_extract_price("Q 85.00"), 85.0)
        self.assertEqual(_extract_price("Q 12,50"), 12.5)

    def test_first_price_token_when_text_concatenated(self):
        # Precio con descuento pegado: 'Q 85.00Q 75.00' -> debe tomar el primero
        self.assertEqual(_extract_price("Q 85.00Q 75.00"), 85.0)

    def test_invalid_or_empty(self):
        self.assertEqual(_extract_price(""), 0.0)
        self.assertEqual(_extract_price("Agotado"), 0.0)


class TestShippingCustomFlagT5(unittest.TestCase):
    RULES = {
        "La Electrónica": {"free_threshold": 150.0, "default_cost": 35.0, "is_pickup_only": False},
        "Electrónica DIY": {"free_threshold": 250.0, "default_cost": 35.0, "is_pickup_only": False},
        "Electrónica RyCH": {"free_threshold": None, "default_cost": 0.0, "is_pickup_only": True},
    }

    def test_flag_only_for_user_custom_costs(self):
        subtotals = {"La Electrónica": 80.0, "Electrónica DIY": 300.0, "Electrónica RyCH": 10.0}
        details = QuoteCalculator.evaluate_shipping_details(subtotals, self.RULES, {"La Electrónica": 45.0})
        la = next(d for d in details if d.store_name == "La Electrónica")
        diy = next(d for d in details if d.store_name == "Electrónica DIY")
        rych = next(d for d in details if d.store_name == "Electrónica RyCH")

        self.assertTrue(la.shipping_was_custom)
        self.assertEqual(la.shipping_cost, 45.0)
        self.assertFalse(diy.shipping_was_custom)   # gratis por umbral
        self.assertFalse(rych.shipping_was_custom)  # retiro en tienda

    def test_free_shipping_not_frozen_as_custom_on_reverify(self):
        """
        Bug T5: el envío gratis (Q0.00) no debe congelarse como costo custom.
        Si al re-verificar el subtotal baja del umbral, debe aplicarse el
        default_cost (Q35) en lugar de Q0.00.
        """
        with tempfile.TemporaryDirectory() as td:
            mgr = HistoryManager(file_path=Path(td) / "history.json")
            prod = Product("Cable", "https://laelectronica.com.gt/products/cable", "La Electrónica", 200.0)
            item = QuoteCalculator.create_quote_item(prod, 1)  # subtotal 200 >= 150 -> gratis
            quote = QuoteCalculator.build_quote(
                quote_id="COT-2026-0001",
                items=[item],
                customer=Customer("Cliente"),
                shipping_details=QuoteCalculator.evaluate_shipping_details({"La Electrónica": 200.0}, self.RULES),
            )
            mgr.save_quote(quote)
            self.assertEqual(quote.shipping_details[0].shipping_cost, 0.0)
            self.assertFalse(quote.shipping_details[0].shipping_was_custom)

            with patch('src.core.history_manager.scrape_product') as mock_scrape, \
                 patch('src.core.history_manager.AppConfig.load') as mock_config:
                from types import SimpleNamespace
                mock_config.return_value = SimpleNamespace(shipping_rules=self.RULES, validity_days=5)
                mock_scrape.return_value = Product(
                    "Cable", "https://laelectronica.com.gt/products/cable", "La Electrónica", 100.0
                )
                cand, _, _ = mgr.check_quote_price_updates("COT-2026-0001")

            la = cand.shipping_details[0]
            self.assertEqual(la.items_subtotal, 100.0)   # bajo el umbral
            self.assertEqual(la.shipping_cost, 35.0)     # default, NO 0.0
            self.assertEqual(cand.total_shipping, 35.0)


class TestOptimizerT4(unittest.TestCase):
    RULES = {
        "La Electrónica": {"free_threshold": 150.0, "default_cost": 35.0, "is_pickup_only": False},
        "Electrónica DIY": {"free_threshold": 250.0, "default_cost": 35.0, "is_pickup_only": False},
        "Electrónica RyCH": {"free_threshold": None, "default_cost": 0.0, "is_pickup_only": True},
    }

    def _cand(self, idx: int, price: float, store: str = "Electrónica RyCH"):
        return SearchResultItem(
            store_name=store, title=f"Item {idx}", url=f"https://x/{idx}", unit_price=price,
            in_stock=True, stock_status="Disponible",
        )

    def test_bounded_search_handles_large_bom(self):
        """3^12 > 50.000 combinaciones -> usa la búsqueda acotada (con poda) y acierta el óptimo."""
        matches = []
        for i in range(12):
            cands = [
                (self._cand(i, 10.0 + i, "Electrónica RyCH"), 0.9),
                (self._cand(i, 11.0 + i, "La Electrónica"), 0.8),
                (self._cand(i, 12.0 + i, "Electrónica DIY"), 0.7),
            ]
            bom = ParsedBOMItem(raw_line=f"1x Item {i}", quantity=1, product_query=f"Item {i}", is_valid=True)
            matches.append(MatchResult(
                bom_item=bom, best_match=cands[0][0], all_candidates=cands,
                confidence_score=0.9, status="ALTA", selected_candidate=cands[0][0], is_confirmed=True,
            ))

        items, missing = find_optimal_mixed_assignment(matches, self.RULES, 10.0)

        self.assertEqual(len(items), 12)
        self.assertEqual(missing, [])
        # El óptimo es todo en RyCH (retiro en tienda, sin envío) con el precio más barato por ítem
        self.assertTrue(all(it.product.store_name == "Electrónica RyCH" for it in items))
        expected = sum(10.0 + i for i in range(12))
        self.assertAlmostEqual(sum(it.subtotal for it in items), expected, places=2)

    def test_greedy_fallback_chooses_cheapest(self):
        cands_a = [(self._cand(0, 50.0), 0.9), (self._cand(0, 30.0), 0.7)]
        cands_b = [(self._cand(1, 20.0), 0.9), (self._cand(1, 25.0), 0.8)]
        items_to_optimize = [
            (0, ParsedBOMItem("1x A", 1, "A", True), cands_a),
            (1, ParsedBOMItem("1x B", 1, "B", True), cands_b),
        ]
        chosen, subtotal, shipping, _ = _greedy_mixed_assignment(items_to_optimize, self.RULES, 1.1)
        self.assertEqual(chosen[0][0].unit_price, 30.0)
        self.assertEqual(chosen[1][0].unit_price, 20.0)
        self.assertEqual(subtotal, 50.0)
        self.assertEqual(shipping, 0.0)  # RyCH pickup


class TestParallelReverifyT8(unittest.TestCase):
    def test_parallel_reverify_preserves_order(self):
        """T8: la re-verificación paralela preserva el orden original de los ítems."""
        with tempfile.TemporaryDirectory() as td:
            mgr = HistoryManager(file_path=Path(td) / "history.json")
            p1 = Product("A", "https://laelectronica.com.gt/products/a", "La Electrónica", 10.0)
            p2 = Product("B", "https://laelectronica.com.gt/products/b", "La Electrónica", 20.0)
            p3 = Product("Manual", "", "La Electrónica", 30.0, is_manual=True)
            items = [
                QuoteCalculator.create_quote_item(p1, 1),
                QuoteCalculator.create_quote_item(p2, 1),
                QuoteCalculator.create_quote_item(p3, 1),
            ]
            quote = QuoteCalculator.build_quote("COT-2026-0001", items, Customer("C"), shipping_details=[])
            mgr.save_quote(quote)

            def side_effect(url):
                if "products/a" in url:
                    return Product("A", url, "La Electrónica", 11.0)
                if "products/b" in url:
                    return Product("B", url, "La Electrónica", 21.0)
                raise ValueError(url)

            with patch('src.core.history_manager.scrape_product') as mock_scrape:
                mock_scrape.side_effect = side_effect
                cand, changes, _ = mgr.check_quote_price_updates("COT-2026-0001")

            self.assertEqual([it.product.name for it in cand.items], ["A", "B", "Manual"])
            self.assertEqual([c["product_name"] for c in changes], ["A", "B", "Manual"])
            self.assertEqual(changes[0]["new_price"], 11.0)
            self.assertEqual(changes[1]["new_price"], 21.0)
            self.assertEqual(changes[2]["status_label"], "Ingreso Manual (Conservado)")


if __name__ == "__main__":
    unittest.main()
