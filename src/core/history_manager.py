import json
import re
import os
import copy
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any, Union
from src.models import Quote, QuoteItem, Customer, StoreShippingDetail, QuoteStatus, InvalidStatusTransitionError
from src.scrapers import scrape_product
from src.core.calculator import QuoteCalculator
from src.config import AppConfig

DEFAULT_HISTORY_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "history.json"

class HistoryManager:
    def __init__(self, file_path: Path = DEFAULT_HISTORY_FILE):
        self.file_path = file_path
        self._ensure_file()

    def _ensure_file(self):
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump([], f, indent=2, ensure_ascii=False)

    def load_all_quotes(self) -> List[Quote]:
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return [Quote.from_dict(q) for q in data]
        except Exception:
            return []

    def save_quote(self, quote: Quote):
        quotes = self.load_all_quotes()
        existing_idx = next((i for i, q in enumerate(quotes) if q.quote_id == quote.quote_id), None)
        if existing_idx is not None:
            quotes[existing_idx] = quote
        else:
            quotes.append(quote)

        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump([q.to_dict() for q in quotes], f, indent=2, ensure_ascii=False)

    def get_quote(self, quote_id: str) -> Optional[Quote]:
        quotes = self.load_all_quotes()
        for q in quotes:
            if q.quote_id.strip().upper() == quote_id.strip().upper():
                return q
        return None

    def update_quote_status(self, quote_id: str, new_status: Union[QuoteStatus, str]) -> Quote:
        """
        Explicitly updates the commercial status of a quote in the history.
        Validates transitions and updates status_updated_at.
        Raises InvalidStatusTransitionError or ValueError if quote not found.
        """
        quote = self.get_quote(quote_id)
        if not quote:
            raise ValueError(f"No se encontró la cotización con ID: {quote_id}")

        quote.change_status(new_status)
        self.save_quote(quote)
        return quote

    def search_quotes(self, query: str = "", status_filter: Optional[str] = None) -> List[Quote]:
        """
        Searches quotes across ID, customer name, customer phone, customer email,
        customer notes, and creation date, optionally filtered by commercial status.
        """
        quotes = self.load_all_quotes()

        # Apply status filter if provided
        if status_filter and status_filter.strip() and status_filter.strip().upper() != "TODOS":
            target_status = status_filter.strip().upper()
            quotes = [q for q in quotes if q.status.upper() == target_status]

        if not query or not query.strip():
            return quotes

        q_clean = query.strip().lower()
        results = []
        for q in quotes:
            match_id = q_clean in q.quote_id.lower()
            match_name = q_clean in q.customer.name.lower()
            match_phone = q_clean in q.customer.phone.lower()
            match_email = q_clean in q.customer.email.lower()
            match_notes = q_clean in q.customer.notes.lower()
            match_date = q_clean in q.date.lower()
            match_status = q_clean in q.status.lower()

            if match_id or match_name or match_phone or match_email or match_notes or match_date or match_status:
                results.append(q)
        return results

    def duplicate_quote(self, quote_id: str, new_customer: Optional[Customer] = None, prefix: str = "COT") -> Quote:
        """
        Duplicates an existing quote as a new independent quote.
        - Assigns a fresh sequential quote_id (e.g. 'COT-2026-0005')
        - Sets version = 1, base_quote_id = None, and status = 'GUARDADA'
        - Sets current date and recalculated validity date
        - Copies items and calculates fresh financial totals and shipping
        - Original quote is 100% untouched.
        """
        original = self.get_quote(quote_id)
        if not original:
            raise ValueError(f"No se encontró la cotización con ID: {quote_id}")

        config = AppConfig.load()
        new_qid = self.get_next_quote_id(prefix or config.quote_prefix)

        # Deep copy customer and items
        customer = copy.deepcopy(new_customer) if new_customer is not None else copy.deepcopy(original.customer)
        items = copy.deepcopy(original.items)

        # Recalculate shipping based on items
        store_subtotals = QuoteCalculator.calculate_store_subtotals(items)
        custom_shipping_costs = {sd.store_name: sd.shipping_cost for sd in original.shipping_details}
        shipping_details = QuoteCalculator.evaluate_shipping_details(
            store_subtotals,
            config.shipping_rules,
            custom_shipping_costs
        )

        duplicated_quote = QuoteCalculator.build_quote(
            quote_id=new_qid,
            items=items,
            customer=customer,
            shipping_details=shipping_details,
            service_fee_percent=original.service_fee_percent,
            validity_days=config.validity_days,
            version=1,
            base_quote_id=None,
            currency_symbol=original.currency_symbol,
            currency_code=original.currency_code
        )
        # Ensure new quote starts in GUARDADA state
        duplicated_quote.status = QuoteStatus.GUARDADA.value

        self.save_quote(duplicated_quote)
        return duplicated_quote

    def get_next_quote_id(self, prefix: str = "COT") -> str:
        quotes = self.load_all_quotes()
        year = datetime.now().year
        pattern_prefix = f"{prefix}-{year}-"
        
        highest_seq = 0
        for q in quotes:
            base = q.base_quote_id or q.quote_id.split('_v')[0]
            if base.startswith(pattern_prefix):
                try:
                    num_str = base.replace(pattern_prefix, "")
                    seq = int(num_str)
                    if seq > highest_seq:
                        highest_seq = seq
                except ValueError:
                    pass

        next_seq = highest_seq + 1
        return f"{pattern_prefix}{next_seq:04d}"

    def get_next_version_info(self, quote_id: str) -> Tuple[str, int, str]:
        """
        Given any quote_id (e.g. 'COT-2026-0001' or 'COT-2026-0001_v2'),
        determines the base quote ID, current highest version in history,
        and returns (new_version_id, new_version_number, base_quote_id).
        """
        quotes = self.load_all_quotes()
        target_quote = self.get_quote(quote_id)
        
        if target_quote and target_quote.base_quote_id:
            base_id = target_quote.base_quote_id
        else:
            base_id = quote_id.split('_v')[0]

        highest_v = 1
        for q in quotes:
            q_base = q.base_quote_id or q.quote_id.split('_v')[0]
            if q_base.upper() == base_id.upper():
                if q.version > highest_v:
                    highest_v = q.version
                if '_v' in q.quote_id:
                    try:
                        v_num = int(q.quote_id.split('_v')[-1])
                        if v_num > highest_v:
                            highest_v = v_num
                    except ValueError:
                        pass

        next_v = highest_v + 1
        new_version_id = f"{base_id}_v{next_v}"
        return new_version_id, next_v, base_id

    def reverify_quote_prices(self, quote_id: str) -> Tuple[Quote, List[Dict[str, Any]]]:
        """
        Re-scrapes all product URLs in the quote, updates unit prices,
        recalculates subtotals and shipping, and returns the updated Quote.
        """
        quote = self.get_quote(quote_id)
        if not quote:
            raise ValueError(f"No se encontró la cotización con ID: {quote_id}")

        config = AppConfig.load()
        changes = []
        updated_items: List[QuoteItem] = []

        for item in quote.items:
            old_price = item.unit_price
            url = item.product.url
            try:
                scraped_prod = scrape_product(url)
                new_price = scraped_prod.unit_price
                price_diff = round(new_price - old_price, 2)
                
                updated_item = QuoteCalculator.create_quote_item(scraped_prod, item.quantity)
                updated_items.append(updated_item)

                changes.append({
                    "product_name": item.product.name,
                    "store": item.product.store_name,
                    "url": url,
                    "old_price": old_price,
                    "new_price": new_price,
                    "diff": price_diff,
                    "in_stock": scraped_prod.in_stock,
                    "stock_status": scraped_prod.stock_status,
                    "status": "Actualizado" if price_diff != 0 else "Sin cambio"
                })
            except Exception as e:
                updated_items.append(item)
                changes.append({
                    "product_name": item.product.name,
                    "store": item.product.store_name,
                    "url": url,
                    "old_price": old_price,
                    "new_price": old_price,
                    "diff": 0.0,
                    "in_stock": item.product.in_stock,
                    "stock_status": "Error al verificar",
                    "status": f"Error: {str(e)}"
                })

        updated_quote = QuoteCalculator.build_quote(
            quote_id=quote.quote_id,
            items=updated_items,
            customer=quote.customer,
            shipping_rules=config.shipping_rules,
            service_fee_percent=quote.service_fee_percent,
            validity_days=config.validity_days,
            version=quote.version,
            base_quote_id=quote.base_quote_id,
            currency_symbol=quote.currency_symbol,
            currency_code=quote.currency_code
        )
        updated_quote.status = quote.status
        updated_quote.status_updated_at = quote.status_updated_at
        
        self.save_quote(updated_quote)
        return updated_quote, changes
