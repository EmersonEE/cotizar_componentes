import re
import difflib
from dataclasses import dataclass
from typing import List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.core.bom_parser import ParsedBOMItem
from src.scrapers.search import SearchResultItem, metasearch

@dataclass
class MatchResult:
    """Represents the search and ranking result for a single BOM item."""
    bom_item: ParsedBOMItem
    best_match: Optional[SearchResultItem]
    all_candidates: List[Tuple[SearchResultItem, float]]  # List of (candidate, score)
    confidence_score: float
    status: str  # "ALTA", "MEDIA", "REVISAR", "NO_ENCONTRADO"

    @property
    def status_badge(self) -> str:
        if self.status == "ALTA":
            return f"🟢 Alta ({int(self.confidence_score * 100)}%)"
        elif self.status == "MEDIA":
            return f"🟡 Media ({int(self.confidence_score * 100)}%)"
        elif self.status == "REVISAR":
            return f"🔴 Revisar ({int(self.confidence_score * 100)}%)"
        else:
            return "❌ No encontrado"

def calculate_match_score(query: str, title: str, in_stock: bool = True) -> float:
    """
    Computes a normalized similarity score in [0.0, 1.0] between the search query
    and a candidate product title.
    
    Weights:
    - Token overlap / recall: 70%
    - String sequence similarity (difflib): 30%
    - Strict numeric validation (e.g. '220', '22', '5', '0.96', '358')
    - In-stock availability factor
    """
    def tokenize(s: str) -> set:
        clean = re.sub(r"[/_\-\+,\.]", " ", s.lower())
        tokens = re.findall(r"[a-z]+|[0-9]+(?:\.[0-9]+)?", clean)
        return set(tokens)

    q_tokens = tokenize(query)
    t_tokens = tokenize(title)

    if not q_tokens or not t_tokens:
        return 0.0

    overlap = q_tokens.intersection(t_tokens)
    recall = len(overlap) / len(q_tokens)

    # Validate numbers strictly: if query has a number like '220' or '22', candidate MUST contain it
    q_nums = {t for t in q_tokens if re.match(r"^\d+(?:\.\d+)?$", t)}
    t_nums = {t for t in t_tokens if re.match(r"^\d+(?:\.\d+)?$", t)}

    num_factor = 1.0
    if q_nums:
        matched_nums = q_nums.intersection(t_nums)
        if not matched_nums:
            num_factor = 0.20  # Severe penalty if required number is completely missing
        else:
            num_factor = len(matched_nums) / len(q_nums)

    seq_ratio = difflib.SequenceMatcher(None, query.lower(), title.lower()).ratio()

    base_score = (0.70 * recall + 0.30 * seq_ratio) * num_factor

    # Out of stock penalty
    if not in_stock:
        base_score *= 0.65

    return round(min(max(base_score, 0.0), 1.0), 3)

def search_single_bom_item(bom_item: ParsedBOMItem) -> MatchResult:
    """
    Searches a single BOM item across stores using metasearch,
    scores candidates, and selects the best recommendation.
    """
    if not bom_item.is_valid or not bom_item.product_query.strip():
        return MatchResult(
            bom_item=bom_item,
            best_match=None,
            all_candidates=[],
            confidence_score=0.0,
            status="NO_ENCONTRADO"
        )

    try:
        candidates = metasearch(bom_item.product_query, max_per_store=5)
    except Exception:
        candidates = []

    if not candidates:
        return MatchResult(
            bom_item=bom_item,
            best_match=None,
            all_candidates=[],
            confidence_score=0.0,
            status="NO_ENCONTRADO"
        )

    scored_candidates: List[Tuple[SearchResultItem, float]] = []
    for cand in candidates:
        score = calculate_match_score(bom_item.product_query, cand.title, cand.in_stock)
        scored_candidates.append((cand, score))

    # Sort descending by score, then in_stock, then unit_price
    scored_candidates.sort(key=lambda x: (x[1], x[0].in_stock, -x[0].unit_price), reverse=True)

    best_candidate, top_score = scored_candidates[0]

    # Assign confidence status based on thresholds
    if top_score >= 0.75 and best_candidate.in_stock:
        status = "ALTA"
    elif top_score >= 0.50:
        status = "MEDIA"
    else:
        status = "REVISAR"

    return MatchResult(
        bom_item=bom_item,
        best_match=best_candidate,
        all_candidates=scored_candidates,
        confidence_score=top_score,
        status=status
    )

def search_bom_items_parallel(
    parsed_items: List[ParsedBOMItem],
    max_workers: int = 5
) -> List[MatchResult]:
    """
    Executes concurrent parallel searches for all BOM items across all stores.
    Preserves the original input order of the BOM items.
    """
    if not parsed_items:
        return []

    # Map future to original index to preserve ordering
    results: List[Optional[MatchResult]] = [None] * len(parsed_items)

    workers = min(len(parsed_items), max_workers)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_index = {
            executor.submit(search_single_bom_item, item): idx
            for idx, item in enumerate(parsed_items)
        }

        for future in as_completed(future_to_index):
            idx = future_to_index[future]
            try:
                match_res = future.result()
                results[idx] = match_res
            except Exception as e:
                # Fallback on worker error
                results[idx] = MatchResult(
                    bom_item=parsed_items[idx],
                    best_match=None,
                    all_candidates=[],
                    confidence_score=0.0,
                    status="NO_ENCONTRADO"
                )

    return [r for r in results if r is not None]
