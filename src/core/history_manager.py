import json
import csv
import os
import copy
import shutil
import logging
import threading
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any, Union, Iterator
from src.models import Quote, QuoteItem, Customer, QuoteStatus
from src.scrapers import scrape_product
from src.core.calculator import QuoteCalculator
from src.config import AppConfig

try:
    import fcntl
except ImportError:  # pragma: no cover - entornos no POSIX (Windows)
    fcntl = None

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "history.json"

# Workers máximos para la re-verificación paralela de precios
MAX_REVERIFY_WORKERS = 5


def _quote_signature(quote: Quote) -> dict:
    """
    Business-level fingerprint of a quote (excludes volatile timestamps).
    Used to distinguish an idempotent re-save from a genuine ID collision.
    """
    return {
        "quote_id": quote.quote_id,
        "version": quote.version,
        "base_quote_id": quote.base_quote_id,
        "status": quote.status,
        "date": quote.date,
        "valid_until": quote.valid_until,
        "customer": quote.customer.to_dict(),
        "items": [item.to_dict() for item in quote.items],
        "shipping_details": [sd.to_dict() for sd in quote.shipping_details],
        "items_subtotal": quote.items_subtotal,
        "service_fee_percent": quote.service_fee_percent,
        "service_fee_amount": quote.service_fee_amount,
        "total_shipping": quote.total_shipping,
        "total": quote.total,
        "currency_symbol": quote.currency_symbol,
        "currency_code": quote.currency_code,
        "validity_days": getattr(quote, "validity_days", 5),
    }


class HistoryManager:
    def __init__(self, file_path: Path = DEFAULT_HISTORY_FILE):
        self.file_path = file_path
        self._lock_path = file_path.with_name(file_path.name + ".lock")
        self._thread_lock = threading.Lock()
        self._ensure_file()

    # ------------------------------------------------------------------
    # Infraestructura de persistencia segura (lock + escritura atómica)
    # ------------------------------------------------------------------
    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Serializes access to history.json across threads AND processes (fcntl)."""
        with self._thread_lock:
            if fcntl is None:  # pragma: no cover - fallback Windows
                yield
                return
            lock_fd = open(self._lock_path, "w")
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()

    def _ensure_file(self):
        with self._locked():
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.file_path.exists():
                self._write_unlocked([])

    def _load_unlocked(self) -> List[Quote]:
        """Loads quotes WITHOUT acquiring the lock. Callers must hold _locked()."""
        if not self.file_path.exists():
            return []

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise ValueError("history.json no contiene una lista de cotizaciones")
            return [Quote.from_dict(q) for q in data]
        except Exception as e:
            logger.error("history.json ilegible (%s). Intentando recuperar desde backup...", e)
            backup = self.file_path.with_suffix(".json.bak")
            if backup.exists():
                try:
                    with open(backup, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    logger.warning(
                        "history.json restaurado desde '%s' (%d cotizaciones).",
                        backup.name, len(data) if isinstance(data, list) else 0,
                    )
                    # Persistir la copia recuperada para dejar el archivo principal válido
                    self._write_unlocked(data)
                    return [Quote.from_dict(q) for q in data]
                except Exception:
                    logger.error("El backup '%s' también es inválido; historial vacío.", backup.name)
                    return []
            logger.error("No hay backup válido; se devuelve historial vacío.")
            return []

    def _write_unlocked(self, quotes: Union[List[Quote], List[dict]]) -> None:
        """
        Writes quotes atomically (temp file + fsync + os.replace) and keeps a
        rolling backup (.bak) of the previous valid state. Callers must hold _locked().
        """
        payload = [q.to_dict() if isinstance(q, Quote) else q for q in quotes]

        # Backup del estado válido anterior
        if self.file_path.exists():
            try:
                shutil.copy2(self.file_path, self.file_path.with_suffix(".json.bak"))
            except Exception as e:
                logger.warning("No se pudo crear backup de history.json: %s", e)

        tmp_path = self.file_path.with_name(self.file_path.name + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, self.file_path)

    # ------------------------------------------------------------------
    # Lectura pública
    # ------------------------------------------------------------------
    def load_all_quotes(self) -> List[Quote]:
        with self._locked():
            return self._load_unlocked()

    def get_quote(self, quote_id: str) -> Optional[Quote]:
        quotes = self.load_all_quotes()
        for q in quotes:
            if q.quote_id.strip().upper() == quote_id.strip().upper():
                return q
        return None

    def effective_status(self, quote: Quote) -> str:
        """
        Estado comercial EFECTIVO de una cotización: si su vigencia (valid_until)
        ya venció y el estado almacenado es GUARDADA/ENVIADA, devuelve VENCIDA
        sin modificar el historial. Las cotizaciones ACEPTADA/RECHAZADA/BORRADOR
        no se auto-vencen.
        """
        if quote.status not in (QuoteStatus.GUARDADA.value, QuoteStatus.ENVIADA.value):
            return quote.status
        try:
            valid_until = datetime.strptime(quote.valid_until, "%d/%m/%Y").date()
            if valid_until < datetime.now().date():
                return QuoteStatus.VENCIDA.value
        except (ValueError, TypeError):
            pass
        return quote.status

    def get_price_history(self, url: Optional[str] = None, sku: Optional[str] = None, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Historial de precios cotizados para un producto (F4). Busca por URL
        (prioritaria) o por SKU en todos los ítems de todas las cotizaciones.
        Devuelve [{quote_id, date, unit_price, store_name}] ordenado por fecha desc.
        """
        url_key = (url or "").strip().lower()
        sku_key = (sku or "").strip().lower()
        if not url_key and not sku_key:
            return []

        history: List[Dict[str, Any]] = []
        for q in self.load_all_quotes():
            for item in q.items:
                matches = False
                if url_key:
                    matches = (item.product.url or "").strip().lower() == url_key
                elif sku_key:
                    matches = (item.product.sku or "").strip().lower() == sku_key
                if matches:
                    history.append({
                        "quote_id": q.quote_id,
                        "date": q.date,
                        "unit_price": item.unit_price,
                        "store_name": item.product.store_name,
                    })

        def _parse_hist_date(h: Dict[str, Any]) -> datetime:
            try:
                return datetime.strptime(h["date"], "%d/%m/%Y")
            except Exception:
                return datetime.min

        history.sort(key=_parse_hist_date, reverse=True)
        return history[:limit]

    def get_frequent_customers(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Retorna los clientes frecuentes extraídos del historial, agregados por nombre
        (excluyendo 'Cliente General'), ordenados por frecuencia descendente y última fecha.
        """
        quotes = self.load_all_quotes()
        cust_map: Dict[str, Dict[str, Any]] = {}

        for q in quotes:
            name = (q.customer.name or "").strip()
            if not name or name.lower() in ("cliente general", "general"):
                continue
            key = name.lower()
            if key not in cust_map:
                cust_map[key] = {
                    "name": name,
                    "phone": q.customer.phone or "",
                    "email": q.customer.email or "",
                    "notes": q.customer.notes or "",
                    "count": 0,
                    "last_date": q.date,
                    "total_spent": 0.0,
                }
            cust_map[key]["count"] += 1
            if q.customer.phone and not cust_map[key]["phone"]:
                cust_map[key]["phone"] = q.customer.phone
            if q.customer.email and not cust_map[key]["email"]:
                cust_map[key]["email"] = q.customer.email
            if q.status == QuoteStatus.ACEPTADA.value:
                cust_map[key]["total_spent"] += q.total

        customers = list(cust_map.values())
        customers.sort(key=lambda c: (c["count"], c["total_spent"]), reverse=True)
        return customers[:limit]

    def get_commercial_analytics(self) -> Dict[str, Any]:
        """
        Calcula y retorna indicadores clave de rendimiento (KPIs) comerciales:
        - Tasa de conversión (Aceptadas / Total)
        - Montos cotizados vs ganados
        - Margen acumulado (ganancia)
        - Desglose por tienda
        """
        quotes = self.load_all_quotes()
        total_quotes = len(quotes)
        
        status_counts = {s.value: 0 for s in QuoteStatus}
        total_quoted_amount = 0.0
        total_sold_amount = 0.0
        total_earned_margin = 0.0
        store_stats: Dict[str, Dict[str, Any]] = {}

        for q in quotes:
            eff_st = self.effective_status(q)
            status_counts[eff_st] = status_counts.get(eff_st, 0) + 1
            total_quoted_amount += q.total

            if eff_st == QuoteStatus.ACEPTADA.value:
                total_sold_amount += q.total
                total_earned_margin += q.service_fee_amount

            for it in q.items:
                sname = it.product.store_name
                if sname not in store_stats:
                    store_stats[sname] = {"count": 0, "subtotal": 0.0}
                store_stats[sname]["count"] += it.quantity
                store_stats[sname]["subtotal"] += it.subtotal

        accepted_count = status_counts.get(QuoteStatus.ACEPTADA.value, 0)
        conversion_rate = round((accepted_count / total_quotes * 100.0), 1) if total_quotes > 0 else 0.0

        return {
            "total_quotes": total_quotes,
            "total_quoted_amount": round(total_quoted_amount, 2),
            "total_sold_amount": round(total_sold_amount, 2),
            "total_earned_margin": round(total_earned_margin, 2),
            "accepted_count": accepted_count,
            "conversion_rate": conversion_rate,
            "status_counts": status_counts,
            "store_stats": store_stats,
            "frequent_customers": self.get_frequent_customers(limit=5),
        }

    def export_history(self, path: Path) -> Path:
        """Exporta el historial completo a un archivo JSON (copia de seguridad/backup manual)."""
        quotes = self.load_all_quotes()
        payload = [q.to_dict() for q in quotes]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        logger.info("Historial exportado a %s (%d cotizaciones).", path, len(quotes))
        return path

    def export_history_csv(self, path: Path) -> Path:
        """Exporta un resumen CSV del historial (una fila por cotización)."""
        quotes = self.load_all_quotes()
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "ID", "Versión", "Estado", "Fecha", "Vigencia", "Cliente", "Teléfono",
                "Ítems", "Subtotal", "Margen %", "Total Envíos", "Total", "Notas Venta",
            ])
            for q in quotes:
                writer.writerow([
                    q.quote_id, q.version, q.status, q.date, q.valid_until,
                    q.customer.name, q.customer.phone, len(q.items),
                    q.items_subtotal, q.service_fee_percent, q.total_shipping,
                    q.total, q.sale_notes,
                ])
        return path

    def import_history(self, path: Path) -> int:
        """
        Importa cotizaciones desde un archivo JSON exportado. Las cotizaciones
        cuyo quote_id ya existe NO se duplican (se conserva la existente).
        Devuelve la cantidad importada.
        """
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("El archivo de importación no contiene una lista de cotizaciones")

        with self._locked():
            quotes = self._load_unlocked()
            existing_ids = {q.quote_id.strip().upper() for q in quotes}
            added = 0
            for raw in data:
                try:
                    q = Quote.from_dict(raw)
                except Exception as e:
                    logger.warning("Importación: se omitió una cotización inválida: %s", e)
                    continue
                if q.quote_id.strip().upper() not in existing_ids:
                    quotes.append(q)
                    existing_ids.add(q.quote_id.strip().upper())
                    added += 1
            if added:
                self._write_unlocked(quotes)
        logger.info("Importación completada: %d cotización(es) agregada(s).", added)
        return added

    def delete_quote(self, quote_id: str) -> int:
        """
        Elimina DEFINITIVAMENTE una cotización y todas sus versiones
        (_vN con base_quote_id == quote_id). Devuelve la cantidad eliminada.
        El backup automático (.bak) queda como red de seguridad.
        """
        target = quote_id.strip().upper()
        with self._locked():
            quotes = self._load_unlocked()
            remaining = [
                q for q in quotes
                if q.quote_id.strip().upper() != target
                and (q.base_quote_id or "").strip().upper() != target
            ]
            removed = len(quotes) - len(remaining)
            if removed > 0:
                self._write_unlocked(remaining)
                logger.info("Cotización '%s' eliminada (%d registro(s) incluyendo versiones).", quote_id, removed)
        return removed

    # ------------------------------------------------------------------
    # Escritura
    # ------------------------------------------------------------------
    def save_quote(self, quote: Quote, force_overwrite: bool = False) -> Quote:
        """
        Persists a quote under lock.
        - force_overwrite=True: the existing record with the same quote_id is
          replaced (used for deliberate updates such as status changes).
        - Otherwise, if a DIFFERENT fresh quote (version <= 1, no base) already
          exists with the same quote_id, the incoming quote is treated as an
          ID collision: a new sequential ID is assigned automatically and the
          original record is preserved.
        - An identical re-save is idempotent (overwrites, no duplication).
        Returns the (possibly reassigned) quote.
        """
        with self._locked():
            quotes = self._load_unlocked()
            existing_idx = next((i for i, q in enumerate(quotes) if q.quote_id == quote.quote_id), None)

            if existing_idx is not None:
                existing = quotes[existing_idx]
                is_fresh = (quote.version <= 1 and quote.base_quote_id is None)
                if (
                    not force_overwrite
                    and is_fresh
                    and _quote_signature(quote) != _quote_signature(existing)
                ):
                    new_id = self._next_id_unlocked(quotes, quote.quote_id.split("-")[0])
                    logger.warning(
                        "Colisión de ID '%s' (dos cotizaciones distintas con el mismo ID). "
                        "Se asigna nuevo ID '%s' y se conserva la original.",
                        quote.quote_id, new_id,
                    )
                    quote.quote_id = new_id
                    quotes.append(quote)
                else:
                    quotes[existing_idx] = quote
            else:
                quotes.append(quote)

            self._write_unlocked(quotes)
        return quote

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
        self.save_quote(quote, force_overwrite=True)
        return quote

    # ------------------------------------------------------------------
    # Búsqueda y utilidades
    # ------------------------------------------------------------------
    def search_quotes(self, query: str = "", status_filter: Optional[str] = None) -> List[Quote]:
        """
        Searches quotes across ID, customer name, customer phone, customer email,
        customer notes, item SKUs, and creation date, optionally filtered by
        commercial status (el filtro usa el estado EFECTIVO: VENCIDA automático).
        """
        quotes = self.load_all_quotes()

        # Apply status filter (estado efectivo, incluye VENCIDA automático)
        if status_filter and status_filter.strip() and status_filter.strip().upper() != "TODOS":
            target_status = status_filter.strip().upper()
            quotes = [q for q in quotes if self.effective_status(q).upper() == target_status]

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
            match_status = q_clean in self.effective_status(q).lower()
            match_sku = any(q_clean in (it.product.sku or "").lower() for it in q.items)

            if match_id or match_name or match_phone or match_email or match_notes or match_date or match_status or match_sku:
                results.append(q)
        return results

    # ------------------------------------------------------------------
    # Numeración y versionado
    # ------------------------------------------------------------------
    def get_next_quote_id(self, prefix: str = "COT") -> str:
        with self._locked():
            quotes = self._load_unlocked()
            return self._next_id_unlocked(quotes, prefix)

    def _next_id_unlocked(self, quotes: List[Quote], prefix: str) -> str:
        """Computes the next sequential quote ID for the current year."""
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

    # ------------------------------------------------------------------
    # Duplicado
    # ------------------------------------------------------------------
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

        # Recalculate shipping based on items preserving ONLY costs explicitly set by the user
        store_subtotals = QuoteCalculator.calculate_store_subtotals(items)
        custom_shipping_costs = {
            sd.store_name: sd.shipping_cost
            for sd in original.shipping_details
            if sd.shipping_was_custom
        }
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
        duplicated_quote.status = QuoteStatus.GUARDADA.value

        self.save_quote(duplicated_quote)
        return duplicated_quote

    # ------------------------------------------------------------------
    # Re-verificación de precios
    # ------------------------------------------------------------------
    @staticmethod
    def _reverify_item(item: QuoteItem) -> Dict[str, Any]:
        """
        Re-scrapes a single item and returns:
        {"updated_item": QuoteItem, "change_info": dict, "stock_alert": Optional[dict]}
        Used by check_quote_price_updates running items in parallel.
        """
        old_price = item.unit_price
        old_subtotal = item.subtotal
        old_in_stock = item.product.in_stock
        url = item.product.url

        # Skip automatic revalidation for manual products or items without valid web URL
        if item.product.is_manual or not url or not url.strip().startswith("http"):
            return {
                "updated_item": copy.deepcopy(item),
                "change_info": {
                    "product_name": item.product.name,
                    "store": item.product.store_name,
                    "url": url,
                    "quantity": item.quantity,
                    "old_price": old_price,
                    "new_price": old_price,
                    "price_diff": 0.0,
                    "old_subtotal": old_subtotal,
                    "new_subtotal": old_subtotal,
                    "subtotal_diff": 0.0,
                    "old_in_stock": old_in_stock,
                    "new_in_stock": old_in_stock,
                    "stock_status": item.product.stock_status,
                    "status_label": "Ingreso Manual (Conservado)"
                },
                "stock_alert": None,
            }

        try:
            scraped_prod = scrape_product(url)
            new_price = scraped_prod.unit_price
            new_in_stock = scraped_prod.in_stock
            stock_status = scraped_prod.stock_status

            updated_item = QuoteCalculator.create_quote_item(scraped_prod, item.quantity)

            price_diff = round(new_price - old_price, 2)
            subtotal_diff = round(updated_item.subtotal - old_subtotal, 2)

            change_info = {
                "product_name": item.product.name,
                "store": item.product.store_name,
                "url": url,
                "quantity": item.quantity,
                "old_price": old_price,
                "new_price": new_price,
                "price_diff": price_diff,
                "old_subtotal": old_subtotal,
                "new_subtotal": updated_item.subtotal,
                "subtotal_diff": subtotal_diff,
                "old_in_stock": old_in_stock,
                "new_in_stock": new_in_stock,
                "stock_status": stock_status,
                "status_label": "Actualizado" if price_diff != 0 else "Sin cambio"
            }

            stock_alert = None
            if old_in_stock != new_in_stock:
                stock_alert = {
                    "product_name": item.product.name,
                    "store": item.product.store_name,
                    "old_stock": "Disponible" if old_in_stock else "Agotado",
                    "new_stock": stock_status
                }

            return {"updated_item": updated_item, "change_info": change_info, "stock_alert": stock_alert}

        except Exception as e:
            logger.warning("Re-verificación: tienda no disponible para '%s' (%s): %s",
                           item.product.name, url, e)
            return {
                "updated_item": copy.deepcopy(item),
                "change_info": {
                    "product_name": item.product.name,
                    "store": item.product.store_name,
                    "url": url,
                    "quantity": item.quantity,
                    "old_price": old_price,
                    "new_price": old_price,
                    "price_diff": 0.0,
                    "old_subtotal": old_subtotal,
                    "new_subtotal": old_subtotal,
                    "subtotal_diff": 0.0,
                    "old_in_stock": old_in_stock,
                    "new_in_stock": old_in_stock,
                    "stock_status": "Tienda no disponible",
                    "status_label": f"Tienda no disponible ({str(e)})"
                },
                "stock_alert": None,
            }

    def check_quote_price_updates(self, quote_id: str) -> Tuple[Quote, List[Dict[str, Any]], Dict[str, Any]]:
        """
        Inspects live prices for all products in quote_id WITHOUT modifying history.
        Preserves original custom shipping costs and calculates candidate new version (e.g. v2).
        Returns:
            (candidate_versioned_quote, item_changes, summary_diff)
        """
        original_quote = self.get_quote(quote_id)
        if not original_quote:
            raise ValueError(f"No se encontró la cotización con ID: {quote_id}")

        config = AppConfig.load()
        new_qid, new_version, base_id = self.get_next_version_info(quote_id)

        item_changes: List[Dict[str, Any]] = []
        updated_items: List[QuoteItem] = []
        stock_alerts: List[Dict[str, Any]] = []

        # Re-verificación paralela de precios (preserva el orden original de los ítems)
        workers = max(1, min(len(original_quote.items), MAX_REVERIFY_WORKERS))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(self._reverify_item, original_quote.items))

        for result in results:
            updated_items.append(result["updated_item"])
            item_changes.append(result["change_info"])
            if result["stock_alert"] is not None:
                stock_alerts.append(result["stock_alert"])

        # Preserve ONLY custom shipping costs explicitly set by the user; automatic
        # costs are recalculated with current rules (fixes envío Q0.00 tras re-verificar)
        custom_shipping_costs = {
            sd.store_name: sd.shipping_cost
            for sd in original_quote.shipping_details
            if sd.shipping_was_custom
        }
        store_subtotals = QuoteCalculator.calculate_store_subtotals(updated_items)
        recalculated_shipping = QuoteCalculator.evaluate_shipping_details(
            store_subtotals,
            config.shipping_rules,
            custom_shipping_costs
        )

        candidate_quote = QuoteCalculator.build_quote(
            quote_id=new_qid,
            items=updated_items,
            customer=copy.deepcopy(original_quote.customer),
            shipping_details=recalculated_shipping,
            service_fee_percent=original_quote.service_fee_percent,
            validity_days=config.validity_days,
            version=new_version,
            base_quote_id=base_id,
            currency_symbol=original_quote.currency_symbol,
            currency_code=original_quote.currency_code
        )
        candidate_quote.status = QuoteStatus.GUARDADA.value

        # Calculate full financial summary diff
        old_shipping_by_store = {sd.store_name: sd.shipping_cost for sd in original_quote.shipping_details}
        new_shipping_by_store = {sd.store_name: sd.shipping_cost for sd in candidate_quote.shipping_details}
        shipping_diff_details = []
        for store, new_sc in new_shipping_by_store.items():
            old_sc = old_shipping_by_store.get(store, 0.0)
            shipping_diff_details.append({
                "store": store,
                "old_shipping": old_sc,
                "new_shipping": new_sc,
                "diff": round(new_sc - old_sc, 2)
            })

        summary_diff = {
            "original_quote_id": original_quote.quote_id,
            "original_version": original_quote.version,
            "candidate_quote_id": candidate_quote.quote_id,
            "candidate_version": candidate_quote.version,
            "old_items_subtotal": original_quote.items_subtotal,
            "new_items_subtotal": candidate_quote.items_subtotal,
            "items_subtotal_diff": round(candidate_quote.items_subtotal - original_quote.items_subtotal, 2),
            "old_service_fee": original_quote.service_fee_amount,
            "new_service_fee": candidate_quote.service_fee_amount,
            "service_fee_diff": round(candidate_quote.service_fee_amount - original_quote.service_fee_amount, 2),
            "old_total_shipping": original_quote.total_shipping,
            "new_total_shipping": candidate_quote.total_shipping,
            "total_shipping_diff": round(candidate_quote.total_shipping - original_quote.total_shipping, 2),
            "old_total": original_quote.total,
            "new_total": candidate_quote.total,
            "total_diff": round(candidate_quote.total - original_quote.total, 2),
            "stock_alerts": stock_alerts,
            "shipping_diff_details": shipping_diff_details
        }

        return candidate_quote, item_changes, summary_diff

    def save_reverified_version(self, candidate_quote: Quote) -> Quote:
        """
        Saves a reverified candidate quote as a brand new version in history.
        Original quote is 100% untouched.
        """
        self.save_quote(candidate_quote)
        return candidate_quote

    def reverify_quote_prices(self, quote_id: str, auto_save_new_version: bool = True) -> Tuple[Quote, List[Dict[str, Any]], Dict[str, Any]]:
        """
        Re-evaluates prices for quote_id.
        If auto_save_new_version is True, saves the new version into history.json.
        Returns (candidate_or_saved_quote, changes, summary_diff).
        """
        candidate_quote, changes, summary_diff = self.check_quote_price_updates(quote_id)
        if auto_save_new_version:
            self.save_reverified_version(candidate_quote)
        return candidate_quote, changes, summary_diff
