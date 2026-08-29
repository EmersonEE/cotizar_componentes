import sys
import os
import logging
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.bom_searcher import MatchResult, ParsedBOMItem, summarize_match_results
from src.scrapers.search import SearchResultItem


def _cand(title="Candidato", price=10.0):
    return SearchResultItem(
        store_name="La Electrónica", title=title, url=f"https://x/{title}",
        unit_price=price, in_stock=True, stock_status="Disponible",
    )


def _match(query, status, with_candidate=True, confirmed=False):
    bom = ParsedBOMItem(raw_line=f"1x {query}", quantity=1, product_query=query, is_valid=True)
    cand = _cand(query) if with_candidate else None
    return MatchResult(
        bom_item=bom,
        best_match=cand,
        all_candidates=[(cand, 0.8)] if cand else [],
        confidence_score=0.8 if cand else 0.0,
        status=status,
        selected_candidate=cand,
        is_confirmed=confirmed,
    )


class TestSummarizeMatchResults(unittest.TestCase):

    def test_counts_by_classification(self):
        results = [
            _match("A", "ALTA"),
            _match("B", "MEDIA"),
            _match("C", "REVISAR"),
            _match("D", "NO_ENCONTRADO", with_candidate=False),
            _match("E", "REVISAR", confirmed=True),  # confirmado -> ya no cuenta como review
        ]
        summary = summarize_match_results(results)

        self.assertEqual(summary["total"], 5)
        self.assertEqual(summary["found"], 4)
        self.assertEqual(summary["unfound"], 1)
        self.assertEqual(summary["media"], 1)
        self.assertEqual(summary["review"], 1)
        self.assertEqual(summary["alta"], 1)

    def test_empty_list(self):
        summary = summarize_match_results([])
        self.assertEqual(summary, {"total": 0, "found": 0, "unfound": 0, "media": 0, "review": 0, "alta": 0})


class TestLoggingSetupOffline(unittest.TestCase):

    def test_httpx_logger_silenced(self):
        from src.logging_setup import setup_logging, NOISY_LOGGERS
        setup_logging(logging.INFO)
        for name in NOISY_LOGGERS:
            self.assertEqual(logging.getLogger(name).level, logging.WARNING,
                             f"El logger '{name}' debe quedar en WARNING")


if __name__ == "__main__":
    unittest.main()
