"""Servicio compartido de flujo de cotización (T9).

Centraliza la lógica de negocio que antes se duplicaba entre el CLI
(src/ui/cli.py) y la Web (app.py): finalización de escenarios BOM, guardado con
dedupe de ítems repetidos y exportación. Las UIs quedan delgadas y el
comportamiento es consistente entre ambas.
"""
from dataclasses import dataclass
from typing import List, Optional

from src.models import QuoteItem, Quote, Customer, QuoteStatus
from src.config import AppConfig
from src.core.calculator import QuoteCalculator
from src.core.history_manager import HistoryManager
from src.core.exporter import QuoteExporter, ExportResult
from src.core.bom_searcher import BOMScenario


def review_scenario_quality(scenario: BOMScenario, history_mgr: HistoryManager,
                            max_price_vs_history_pct: float = 0.4) -> List[str]:
    """
    Advertencias heurísticas de calidad sobre un escenario antes de exportar:
    compara el precio actual de cada ítem con el promedio histórico del mismo URL
    (F4) y reporta diferencias superiores a max_price_vs_history_pct (40%).
    Devuelve una lista de advertencias legibles (vacía si todo está bien).
    """
    warnings: List[str] = []
    for i, item in enumerate(scenario.items, 1):
        hist = history_mgr.get_price_history(url=item.product.url, limit=5)
        if not hist:
            continue
        avg = sum(h["unit_price"] for h in hist) / len(hist)
        if avg > 0 and abs(item.unit_price - avg) / avg > max_price_vs_history_pct:
            warnings.append(
                f"Ítem #{i} '{item.product.name[:45]}': precio actual Q{item.unit_price:,.2f} "
                f"vs histórico promedio Q{avg:,.2f} (diferencia > {int(max_price_vs_history_pct * 100)}%)."
            )
    return warnings


@dataclass
class SaveResult:
    quote: Quote
    export: ExportResult
    merged_count: int = 0  # ítems repetidos fusionados durante el guardado


class QuoteFlowService:
    def __init__(
        self,
        config: Optional[AppConfig] = None,
        history_mgr: Optional[HistoryManager] = None,
        exporter: Optional[QuoteExporter] = None,
    ):
        self.config = config or AppConfig.load()
        self.history_mgr = history_mgr or HistoryManager()
        self.exporter = exporter or QuoteExporter()

    def finalize_scenario_quote(
        self,
        items: List[QuoteItem],
        customer: Customer,
        service_fee_percent: float,
        shipping_details: Optional[List] = None,
    ) -> Quote:
        """
        Construye la cotización final (estado GUARDADA, ID secuencial nuevo)
        a partir de los ítems elegidos de un escenario BOM o de una cotización manual.
        """
        quote_id = self.history_mgr.get_next_quote_id(self.config.quote_prefix)

        if shipping_details is None:
            store_subtotals = QuoteCalculator.calculate_store_subtotals(items)
            shipping_details = QuoteCalculator.evaluate_shipping_details(
                store_subtotals, self.config.shipping_rules
            )

        quote = QuoteCalculator.build_quote(
            quote_id=quote_id,
            items=items,
            customer=customer,
            shipping_details=shipping_details,
            service_fee_percent=service_fee_percent,
            validity_days=self.config.validity_days,
            currency_symbol=self.config.currency_symbol,
            currency_code=self.config.currency_code,
        )
        quote.status = QuoteStatus.GUARDADA.value
        return quote

    def save_and_export(self, quote: Quote) -> SaveResult:
        """
        Fusiona ítems repetidos (F8), recalcula montos y envíos, guarda en el
        historial y exporta todos los documentos (CSV + HTML/PDF Cliente e Interna).
        Devuelve el resultado con la cantidad de ítems fusionados.
        """
        merged = QuoteCalculator.merge_duplicate_items(quote.items)
        merged_count = len(quote.items) - len(merged)

        if merged_count:
            # Recalcular envíos preservando costos custom y reconstruir montos
            custom_costs = {
                sd.store_name: sd.shipping_cost
                for sd in quote.shipping_details
                if sd.shipping_was_custom
            }
            store_subtotals = QuoteCalculator.calculate_store_subtotals(merged)
            shipping_details = QuoteCalculator.evaluate_shipping_details(
                store_subtotals, self.config.shipping_rules, custom_costs
            )
            quote = QuoteCalculator.build_quote(
                quote_id=quote.quote_id,
                items=merged,
                customer=quote.customer,
                shipping_details=shipping_details,
                service_fee_percent=quote.service_fee_percent,
                validity_days=quote.validity_days,
                version=quote.version,
                base_quote_id=quote.base_quote_id,
                currency_symbol=quote.currency_symbol,
                currency_code=quote.currency_code,
            )
            quote.status = QuoteStatus.GUARDADA.value

        saved = self.history_mgr.save_quote(quote)
        export = self.exporter.export_all(saved, self.config.business)
        return SaveResult(quote=saved, export=export, merged_count=merged_count)
