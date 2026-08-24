import json
import re
import os
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any
from src.models import Quote, QuoteItem, StoreShippingDetail
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

    def get_next_quote_id(self, prefix: str = "COT") -> str:
        quotes = self.load_all_quotes()
        year = datetime.now().year
        pattern_prefix = f"{prefix}-{year}-"
        
        highest_seq = 0
        for q in quotes:
            # Check base IDs without version suffix
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

        # Find all versions for this base_id
        highest_v = 1
        for q in quotes:
            q_base = q.base_quote_id or q.quote_id.split('_v')[0]
            if q_base.upper() == base_id.upper():
                if q.version > highest_v:
                    highest_v = q.version
                # Also check version suffix in ID if version property was 1
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

        # Recalculate quote with updated prices & re-evaluated shipping
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
        
        self.save_quote(updated_quote)
        return updated_quote, changes
