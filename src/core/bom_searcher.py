import re
import difflib
import itertools
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any
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
    selected_candidate: Optional[SearchResultItem] = None
    is_confirmed: bool = False

    def __post_init__(self):
        if self.selected_candidate is None:
            self.selected_candidate = self.best_match

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

    @property
    def requires_review_confirmation(self) -> bool:
        return self.status == "REVISAR" and not self.is_confirmed

    def get_best_match_for_store(self, store_name: str) -> Optional[Tuple[SearchResultItem, float]]:
        """Returns the best matching candidate for a specific store."""
        cands = [
            (cand, score) for cand, score in self.all_candidates
            if cand.store_name.strip().lower() == store_name.strip().lower()
        ]
        if not cands:
            return None
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

def clean_for_matching(text: str) -> str:
    """Normalizes text for robust electronic component matching."""
    s = text.lower().strip()
    # Strip leading quantities (e.g. '2x ', '50x ', '10 ')
    s = re.sub(r'^(?:\d+\s*[xX×\*\-]\s*|\d+\s+)', '', s).strip()
    # Display synonyms & resolutions
    s = re.sub(r'\b(?:128x64|12864|128\*64)\b', '0.96', s)
    s = re.sub(r'\b(?:pantallas?|displays?|screens?)\b', 'display', s)
    s = re.sub(r'0\.96[\"\'\s]*(?:pulgadas?|pulg|inch)?', '0.96', s)
    s = re.sub(r'1\.3[\"\'\s]*(?:pulgadas?|pulg|inch)?', '1.3', s)
    # Cable Dupont formats
    s = re.sub(r'\b(?:macho[\s\-_]*a?[\s\-_]*hembra|m[\s\-_]*h|m/h)\b', 'macho_hembra', s)
    s = re.sub(r'\b(?:macho[\s\-_]*a?[\s\-_]*macho|m[\s\-_]*m|m/m)\b', 'macho_macho', s)
    s = re.sub(r'\b(?:hembra[\s\-_]*a?[\s\-_]*hembra|h[\s\-_]*h|h/h)\b', 'hembra_hembra', s)
    # General clean
    s = re.sub(r"[/_\-\+,\.]", " ", s)
    return s

def calculate_match_score(query: str, title: str, in_stock: bool = True) -> float:
    """
    Computes a normalized similarity score in [0.0, 1.0] between the search query
    and a candidate product title.
    """
    q_clean = clean_for_matching(query)
    t_clean = clean_for_matching(title)

    def tokenize(s: str) -> set:
        tokens = re.findall(r"[a-z]+|[0-9]+(?:\.[0-9]+)?", s)
        return set(tokens)

    q_tokens = tokenize(q_clean)
    t_tokens = tokenize(t_clean)

    if not q_tokens or not t_tokens:
        return 0.0

    overlap = q_tokens.intersection(t_tokens)
    recall = len(overlap) / len(q_tokens)

    # Validate numbers strictly (e.g. '220', '22', '5', '0.96', '358', '830', '05', '04', '90')
    q_nums = {t for t in q_tokens if re.match(r"^\d+(?:\.\d+)?$", t)}
    t_nums = {t for t in t_tokens if re.match(r"^\d+(?:\.\d+)?$", t)}

    num_factor = 1.0
    if q_nums:
        matched_nums = q_nums.intersection(t_nums)
        if not matched_nums:
            num_factor = 0.40  # Moderate penalty if key number differs
        else:
            num_factor = len(matched_nums) / len(q_nums)

    seq_ratio = difflib.SequenceMatcher(None, q_clean, t_clean).ratio()
    base_score = (0.70 * recall + 0.30 * seq_ratio) * num_factor

    if not in_stock:
        base_score *= 0.85

    return round(min(max(base_score, 0.0), 1.0), 3)

def search_single_bom_item(bom_item: ParsedBOMItem) -> MatchResult:
    """Searches a single BOM item across stores, filters valid prices, scores candidates, and selects best match."""
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

    # Filter out results with invalid or zero/negative prices
    valid_candidates = [
        c for c in candidates
        if c.unit_price is not None and c.unit_price > 0.0
    ]

    if not valid_candidates:
        return MatchResult(
            bom_item=bom_item,
            best_match=None,
            all_candidates=[],
            confidence_score=0.0,
            status="NO_ENCONTRADO"
        )

    scored_candidates: List[Tuple[SearchResultItem, float]] = []
    for cand in valid_candidates:
        score = calculate_match_score(bom_item.product_query, cand.title, cand.in_stock)
        if score >= 0.10:
            scored_candidates.append((cand, score))

    if not scored_candidates:
        return MatchResult(
            bom_item=bom_item,
            best_match=None,
            all_candidates=[],
            confidence_score=0.0,
            status="NO_ENCONTRADO"
        )

    # Sort candidates by score descending, in_stock True first, price ascending
    scored_candidates.sort(key=lambda x: (x[1], x[0].in_stock, -x[0].unit_price), reverse=True)
    best_candidate, top_score = scored_candidates[0]

    if top_score >= 0.65 and best_candidate.in_stock:
        status = "ALTA"
    elif top_score >= 0.45:
        status = "MEDIA"
    elif top_score >= 0.25:
        status = "REVISAR"
    else:
        status = "NO_ENCONTRADO"
        best_candidate = None

    return MatchResult(
        bom_item=bom_item,
        best_match=best_candidate,
        all_candidates=scored_candidates,
        confidence_score=top_score if best_candidate else 0.0,
        status=status,
        selected_candidate=best_candidate
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

def find_optimal_mixed_assignment(
    match_results: List[MatchResult],
    shipping_rules: Dict[str, Dict[str, Any]],
    service_fee_percent: float
) -> Tuple[List[QuoteItem], List[str]]:
    """
    Finds the exact globally optimal combination of store items for the mixed quote scenario
    by minimizing: Total = Subtotal Componentes + Servicio + Total Envíos.
    Preserves the original line order of the BOM items.
    """
    items_to_optimize = []
    missing_queries = []
    original_indices = []

    for idx, m in enumerate(match_results):
        # Determine candidate options for this item
        cand_options = []
        for store in SUPPORTED_STORES:
            best_store_cand = m.get_best_match_for_store(store)
            if best_store_cand:
                cand, score = best_store_cand
                if cand.unit_price > 0:
                    cand_options.append((cand, score))

        # Fallback to selected_candidate or best_match if no store candidate found
        if not cand_options and m.selected_candidate:
            cand_options.append((m.selected_candidate, m.confidence_score))

        if cand_options:
            items_to_optimize.append((idx, m.bom_item, cand_options))
            original_indices.append(idx)
        else:
            missing_queries.append(m.bom_item.product_query)

    if not items_to_optimize:
        return [], missing_queries

    fee_multiplier = 1.0 + (service_fee_percent / 100.0)

    # Combinatorial search: evaluate all combinations of candidate choices
    # For each item, candidate options are at most 3 (one per store)
    choice_lists = [cands for _, _, cands in items_to_optimize]
    
    best_cost = float('inf')
    best_total_score = -1.0
    best_combination: Optional[List[Tuple[SearchResultItem, float]]] = None

    # Use branch and bound / product iteration
    # Since len(items_to_optimize) is typical BOM (<= 20) and choices <= 3:
    # If total combinations <= 50,000, exhaustive cartesian product is instantaneous.
    total_combinations = 1
    for cl in choice_lists:
        total_combinations *= len(cl)

    if total_combinations <= 50000:
        for combo in itertools.product(*choice_lists):
            # combo is tuple of (SearchResultItem, score)
            # Calculate store subtotals
            store_subtotals: Dict[str, float] = {}
            total_items_subtotal = 0.0
            sum_scores = 0.0

            for (_, bom_item, _), (cand, score) in zip(items_to_optimize, combo):
                sub = round(cand.unit_price * bom_item.quantity, 2)
                store_subtotals[cand.store_name] = store_subtotals.get(cand.store_name, 0.0) + sub
                total_items_subtotal += sub
                sum_scores += score

            # Calculate shipping for this store configuration
            shipping_details = QuoteCalculator.evaluate_shipping_details(store_subtotals, shipping_rules)
            total_shipping = sum(sd.shipping_cost for sd in shipping_details)

            grand_total = round((total_items_subtotal * fee_multiplier) + total_shipping, 2)

            if grand_total < best_cost or (grand_total == best_cost and sum_scores > best_total_score):
                best_cost = grand_total
                best_total_score = sum_scores
                best_combination = combo
    else:
        # Branch and bound for very large BOMs
        def search_bb(item_idx, current_subtotals, current_items_subtotal, current_combo, current_score):
            nonlocal best_cost, best_total_score, best_combination
            if item_idx == len(items_to_optimize):
                shipping_details = QuoteCalculator.evaluate_shipping_details(current_subtotals, shipping_rules)
                total_shipping = sum(sd.shipping_cost for sd in shipping_details)
                grand_total = round((current_items_subtotal * fee_multiplier) + total_shipping, 2)
                if grand_total < best_cost or (grand_total == best_cost and current_score > best_total_score):
                    best_cost = grand_total
                    best_total_score = current_score
                    best_combination = list(current_combo)
                return

            _, bom_item, cands = items_to_optimize[item_idx]
            for cand, score in cands:
                sub = round(cand.unit_price * bom_item.quantity, 2)
                st_name = cand.store_name
                new_subtotals = current_subtotals.copy()
                new_subtotals[st_name] = new_subtotals.get(st_name, 0.0) + sub
                search_bb(
                    item_idx + 1,
                    new_subtotals,
                    current_items_subtotal + sub,
                    current_combo + [(cand, score)],
                    current_score + score
                )

        search_bb(0, {}, 0.0, [], 0.0)

    # Build QuoteItem list in original BOM line order
    optimal_items: List[QuoteItem] = []
    if best_combination:
        for (orig_idx, bom_item, _), (chosen_cand, _) in zip(items_to_optimize, best_combination):
            prod = Product(
                name=chosen_cand.title,
                url=chosen_cand.url,
                store_name=chosen_cand.store_name,
                unit_price=chosen_cand.unit_price,
                in_stock=chosen_cand.in_stock,
                stock_status=chosen_cand.stock_status,
                image_url=chosen_cand.image_url
            )
            item = QuoteCalculator.create_quote_item(prod, bom_item.quantity)
            optimal_items.append(item)

    return optimal_items, missing_queries

def build_all_bom_scenarios(
    match_results: List[MatchResult],
    customer: Customer,
    config: AppConfig,
    service_fee_percent: float,
    temp_quote_prefix: str = "PREVIEW"
) -> List[BOMScenario]:
    """
    Builds the 4 quote scenarios from the search results:
    1. Opción 1: Cotización Mixta Óptima (Minimiza componentes + envíos + servicio)
    2. Opción 2: Todo en Electrónica RyCH
    3. Opción 3: Todo en La Electrónica
    4. Opción 4: Todo en Electrónica DIY
    Preserves original line order.
    """
    total_requested = len(match_results)
    scenarios: List[BOMScenario] = []

    # ----------------------------------------------------
    # Escenario 1: Cotización Mixta Óptima
    # ----------------------------------------------------
    mixed_items, mixed_missing = find_optimal_mixed_assignment(
        match_results=match_results,
        shipping_rules=config.shipping_rules,
        service_fee_percent=service_fee_percent
    )

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
            if store_match and store_match[0].unit_price > 0:
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
