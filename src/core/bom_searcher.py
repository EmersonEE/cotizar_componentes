import re
import difflib
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.models import Product, QuoteItem, Quote, Customer, BusinessInfo
from src.config import AppConfig
from src.core.bom_parser import ParsedBOMItem
from src.core.calculator import QuoteCalculator
from src.scrapers.search import SearchResultItem, metasearch
from src.scrapers import scrape_product

SUPPORTED_STORES = ["Electrónica RyCH", "La Electrónica", "Electrónica DIY"]

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

    def get_best_match_for_store(self, store_name: str) -> Optional[Tuple[SearchResultItem, float]]:
        """Returns the best matching candidate for a specific store."""
        cands = [
            (cand, score) for cand, score in self.all_candidates
            if cand.store_name.strip().lower() == store_name.strip().lower()
        ]
        if not cands:
            return None
        # Sort by in_stock (True first), then score descending, then price ascending
        cands.sort(key=lambda x: (x[0].in_stock, x[1], -x[0].unit_price), reverse=True)
        return cands[0]

@dataclass
class BOMScenario:
    """Represents one of the 4 quote scenarios generated from a BOM list."""
    scenario_id: int
    title: str
    store_name: Optional[str]  # None for Mixed, or store name
    items: List[QuoteItem]
    missing_queries: List[str]
    total_requested: int
    total_found: int
    quote: Quote

    @property
    def is_complete(self) -> bool:
        return self.total_found == self.total_requested and self.total_requested > 0

    @property
    def coverage_pct(self) -> int:
        if self.total_requested == 0:
            return 0
        return int((self.total_found / self.total_requested) * 100)

    @property
    def coverage_label(self) -> str:
        if self.is_complete:
            return f"✔ Completa ({self.total_found}/{self.total_requested})"
        else:
            return f"⚠️ Parcial ({self.total_found}/{self.total_requested})"

def calculate_match_score(query: str, title: str, in_stock: bool = True) -> float:
    """
    Computes a normalized similarity score in [0.0, 1.0] between the search query
    and a candidate product title.
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

    # Validate numbers strictly (e.g. '220', '22', '5', '0.96', '358')
    q_nums = {t for t in q_tokens if re.match(r"^\d+(?:\.\d+)?$", t)}
    t_nums = {t for t in t_tokens if re.match(r"^\d+(?:\.\d+)?$", t)}

    num_factor = 1.0
    if q_nums:
        matched_nums = q_nums.intersection(t_nums)
        if not matched_nums:
            num_factor = 0.20  # Severe penalty if required number is missing
        else:
            num_factor = len(matched_nums) / len(q_nums)

    seq_ratio = difflib.SequenceMatcher(None, query.lower(), title.lower()).ratio()

    base_score = (0.70 * recall + 0.30 * seq_ratio) * num_factor

    if not in_stock:
        base_score *= 0.65

    return round(min(max(base_score, 0.0), 1.0), 3)

def search_single_bom_item(bom_item: ParsedBOMItem) -> MatchResult:
    """Searches a single BOM item across stores, scores candidates, and selects best match."""
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

    scored_candidates.sort(key=lambda x: (x[1], x[0].in_stock, -x[0].unit_price), reverse=True)

    best_candidate, top_score = scored_candidates[0]

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
    """Executes concurrent parallel searches for all BOM items preserving order."""
    if not parsed_items:
        return []

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
                results[idx] = future.result()
            except Exception:
                results[idx] = MatchResult(
                    bom_item=parsed_items[idx],
                    best_match=None,
                    all_candidates=[],
                    confidence_score=0.0,
                    status="NO_ENCONTRADO"
                )

    return [r for r in results if r is not None]

def build_all_bom_scenarios(
    match_results: List[MatchResult],
    customer: Customer,
    config: AppConfig,
    service_fee_percent: float,
    temp_quote_prefix: str = "PREVIEW"
) -> List[BOMScenario]:
    """
    Builds the 4 quote scenarios from the search results:
    1. Opción 1: Cotización Mixta (Mejor precio combinado entre las 3 tiendas)
    2. Opción 2: Todo en Electrónica RyCH
    3. Opción 3: Todo en La Electrónica
    4. Opción 4: Todo en Electrónica DIY
    """
    total_requested = len(match_results)
    scenarios: List[BOMScenario] = []

    # ----------------------------------------------------
    # Escenario 1: Cotización Mixta (Mejor Combinación)
    # ----------------------------------------------------
    mixed_items: List[QuoteItem] = []
    mixed_missing: List[str] = []

    for m in match_results:
        if m.best_match:
            prod = Product(
                name=m.best_match.title,
                url=m.best_match.url,
                store_name=m.best_match.store_name,
                unit_price=m.best_match.unit_price,
                in_stock=m.best_match.in_stock,
                stock_status=m.best_match.stock_status,
                image_url=m.best_match.image_url
            )
            mixed_items.append(QuoteCalculator.create_quote_item(prod, m.bom_item.quantity))
        else:
            mixed_missing.append(m.bom_item.product_query)

    store_subtotals_mixed = QuoteCalculator.calculate_store_subtotals(mixed_items) if mixed_items else {}
    shipping_mixed = QuoteCalculator.evaluate_shipping_details(store_subtotals_mixed, config.shipping_rules)
    quote_mixed = QuoteCalculator.build_quote(
        quote_id=f"{temp_quote_prefix}-MIXTA",
        items=mixed_items if mixed_items else [QuoteItem(Product("Sin componentes", "", "N/A", 0.0), 1, 0.0, 0.0)],
        customer=customer,
        shipping_details=shipping_mixed,
        service_fee_percent=service_fee_percent,
        validity_days=config.validity_days,
        currency_symbol=config.currency_symbol,
        currency_code=config.currency_code
    )

    scenarios.append(BOMScenario(
        scenario_id=1,
        title="Cotización Mixta (Mejor Precio Combinado)",
        store_name=None,
        items=mixed_items,
        missing_queries=mixed_missing,
        total_requested=total_requested,
        total_found=len(mixed_items),
        quote=quote_mixed
    ))

    # ----------------------------------------------------
    # Escenarios 2, 3, 4: Todo en una Tienda Específica
    # ----------------------------------------------------
    for s_idx, store_name in enumerate(SUPPORTED_STORES, start=2):
        store_items: List[QuoteItem] = []
        store_missing: List[str] = []

        for m in match_results:
            store_match = m.get_best_match_for_store(store_name)
            if store_match:
                cand, score = store_match
                prod = Product(
                    name=cand.title,
                    url=cand.url,
                    store_name=cand.store_name,
                    unit_price=cand.unit_price,
                    in_stock=cand.in_stock,
                    stock_status=cand.stock_status,
                    image_url=cand.image_url
                )
                store_items.append(QuoteCalculator.create_quote_item(prod, m.bom_item.quantity))
            else:
                store_missing.append(m.bom_item.product_query)

        store_subtotals = QuoteCalculator.calculate_store_subtotals(store_items) if store_items else {}
        store_shipping = QuoteCalculator.evaluate_shipping_details(store_subtotals, config.shipping_rules)
        store_quote = QuoteCalculator.build_quote(
            quote_id=f"{temp_quote_prefix}-{store_name.upper().replace(' ', '_')}",
            items=store_items if store_items else [QuoteItem(Product("Sin componentes", "", store_name, 0.0), 1, 0.0, 0.0)],
            customer=customer,
            shipping_details=store_shipping,
            service_fee_percent=service_fee_percent,
            validity_days=config.validity_days,
            currency_symbol=config.currency_symbol,
            currency_code=config.currency_code
        )

        scenarios.append(BOMScenario(
            scenario_id=s_idx,
            title=f"Todo en {store_name}",
            store_name=store_name,
            items=store_items,
            missing_queries=store_missing,
            total_requested=total_requested,
            total_found=len(store_items),
            quote=store_quote
        ))

    return scenarios
