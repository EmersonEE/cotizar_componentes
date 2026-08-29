import sys
import os
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.models import Product, Customer, QuoteStatus
from src.core.calculator import QuoteCalculator
from src.core.history_manager import HistoryManager
from src.core.bom_searcher import BOMScenario
from src.services.quote_flow import review_scenario_quality


def _build_scenario(items, customer=Customer("Cliente")):
    quote = QuoteCalculator.build_quote(
        quote_id="PREVIEW-TEST",
        items=items,
        customer=customer,
        shipping_details=[],
        service_fee_percent=10.0,
    )
    quote.status = QuoteStatus.GUARDADA.value
    return BOMScenario(
        scenario_id=1,
        title="Test",
        store_name=None,
        items=items,
        missing_queries=[],
        total_requested=len(items),
        total_found=len(items),
        quote=quote,
        item_queries=[f"Consulta {i}" for i in range(1, len(items) + 1)],
    )


class TestReviewScenarioQualityOffline(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.mgr = HistoryManager(file_path=Path(self.test_dir) / "history.json")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _save_history_price(self, url, price, qid="COT-2026-0001"):
        prod = Product("Histórico", url, "La Electrónica", price)
        q = QuoteCalculator.build_quote(
            qid, [QuoteCalculator.create_quote_item(prod, 1)], Customer("C"), shipping_details=[]
        )
        self.mgr.save_quote(q)

    def test_warns_when_price_far_from_history(self):
        self._save_history_price("https://x.com/led", 50.0)
        prod = Product("LED rojo 5 mm", "https://x.com/led", "La Electrónica", 100.0)
        scenario = _build_scenario([QuoteCalculator.create_quote_item(prod, 1)])

        warnings = review_scenario_quality(scenario, self.mgr)
        self.assertEqual(len(warnings), 1)
        self.assertIn("100.00", warnings[0])
        self.assertIn("50.00", warnings[0])

    def test_no_warning_when_price_similar_to_history(self):
        self._save_history_price("https://x.com/led", 50.0)
        prod = Product("LED rojo 5 mm", "https://x.com/led", "La Electrónica", 55.0)
        scenario = _build_scenario([QuoteCalculator.create_quote_item(prod, 1)])
        self.assertEqual(review_scenario_quality(scenario, self.mgr), [])

    def test_no_warning_without_history(self):
        prod = Product("LED rojo 5 mm", "https://x.com/nuevo", "La Electrónica", 100.0)
        scenario = _build_scenario([QuoteCalculator.create_quote_item(prod, 1)])
        self.assertEqual(review_scenario_quality(scenario, self.mgr), [])

    def test_custom_threshold(self):
        self._save_history_price("https://x.com/led", 100.0)
        prod = Product("LED rojo 5 mm", "https://x.com/led", "La Electrónica", 130.0)  # +30%
        scenario = _build_scenario([QuoteCalculator.create_quote_item(prod, 1)])
        self.assertEqual(review_scenario_quality(scenario, self.mgr, max_price_vs_history_pct=0.4), [])
        self.assertEqual(len(review_scenario_quality(scenario, self.mgr, max_price_vs_history_pct=0.2)), 1)


if __name__ == "__main__":
    unittest.main()
