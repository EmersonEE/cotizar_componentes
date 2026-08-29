"""Chequeo de salud de los scrapers de las 3 tiendas.

Verifica que cada tienda sigue parseando correctamente probando (a) el scraping
de una URL de producto conocida y (b) la búsqueda por término, midiendo latencia
y reportando precios/stock. Sirve para detectar cambios de HTML en las tiendas
antes de que afecten cotizaciones reales.
"""
import time
import logging
from typing import List, Dict, Any

from src.stores import STORES
from src.scrapers import scrape_product

logger = logging.getLogger(__name__)

# URLs de productos conocidos por tienda (ajustables si una tienda los descontinúa).
KNOWN_PRODUCT_URLS = {
    "La Electrónica": "https://laelectronica.com.gt/products/multimetro-zoyi-zt-225-auto-rango-ultra-preciso",
    "Electrónica DIY": "https://www.electronicadiy.com/products/fnirsi-hrm-10",
    "Electrónica RyCH": "https://electronicarych.com/shop/al-22vi-al-22vi-alambre-1mt-calibre-22-violeta-estanado-7381",
}

SEARCH_FUNCTION_NAMES = {
    "La Electrónica": "search_la_electronica",
    "Electrónica DIY": "search_electronica_diy",
    "Electrónica RyCH": "search_electronica_rych",
}

DEFAULT_SEARCH_QUERY = "ESP32"


def check_store_product(store_name: str, url: str) -> Dict[str, Any]:
    """Scrapea una URL de producto conocida y reporta estado, precio, stock y latencia."""
    if not url or not url.strip():
        return {
            "ok": False, "name": "", "price": 0.0, "stock": "",
            "latency_s": 0.0, "error": "Sin URL de prueba configurada",
        }
    t0 = time.monotonic()
    try:
        prod = scrape_product(url)
        latency = round(time.monotonic() - t0, 2)
        ok = prod.unit_price > 0 and bool(prod.name.strip())
        return {
            "ok": ok,
            "name": prod.name,
            "price": prod.unit_price,
            "stock": prod.stock_status,
            "latency_s": latency,
            "error": "" if ok else "Precio o nombre inválidos (posible cambio de HTML)",
        }
    except Exception as e:
        latency = round(time.monotonic() - t0, 2)
        logger.debug("check_store_product falló para %s (%s): %s", store_name, url, e)
        return {
            "ok": False, "name": "", "price": 0.0, "stock": "",
            "latency_s": latency, "error": str(e),
        }


def check_store_search(store_name: str, query: str = DEFAULT_SEARCH_QUERY, limit: int = 3) -> Dict[str, Any]:
    """Ejecuta la búsqueda de una tienda y verifica que devuelva resultados con precio válido."""
    fn_name = SEARCH_FUNCTION_NAMES.get(store_name)
    if fn_name is None:
        return {"ok": False, "results": 0, "latency_s": 0.0, "error": "Sin función de búsqueda para la tienda"}

    # Resolución dinámica para que los mocks de tests funcionen y la tienda no quede fija
    from src.scrapers import search as search_module
    search_fn = getattr(search_module, fn_name, None)
    if search_fn is None:
        return {"ok": False, "results": 0, "latency_s": 0.0, "error": f"Función '{fn_name}' no encontrada"}

    t0 = time.monotonic()
    try:
        results = search_fn(query, limit=limit)
        latency = round(time.monotonic() - t0, 2)
        valid = [r for r in results if r.unit_price > 0]
        return {
            "ok": len(valid) > 0,
            "results": len(valid),
            "latency_s": latency,
            "error": "" if valid else "Sin resultados con precio válido",
        }
    except Exception as e:
        latency = round(time.monotonic() - t0, 2)
        logger.debug("check_store_search falló para %s: %s", store_name, e)
        return {"ok": False, "results": 0, "latency_s": latency, "error": str(e)}


def run_store_health_check(query: str = DEFAULT_SEARCH_QUERY, search_limit: int = 3) -> List[Dict[str, Any]]:
    """Ejecuta el chequeo completo (producto + búsqueda) para cada tienda del registro."""
    results: List[Dict[str, Any]] = []
    for store in STORES:
        product = check_store_product(store.name, KNOWN_PRODUCT_URLS.get(store.name, ""))
        search = check_store_search(store.name, query, search_limit)
        results.append({
            "store_name": store.name,
            "product": product,
            "search": search,
            "overall_ok": bool(product["ok"] and search["ok"]),
        })
    return results


def print_health_report(results: List[Dict[str, Any]], as_json: bool = False) -> None:
    """Imprime el reporte en tabla legible (rich) o en JSON."""
    if as_json:
        import json
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title="🔍 Chequeo de Salud de Tiendas", box=None)
        table.add_column("Tienda", style="bold cyan")
        table.add_column("Producto", style="white")
        table.add_column("Precio", justify="right", style="green")
        table.add_column("Stock", justify="center")
        table.add_column("Búsqueda", justify="center")
        table.add_column("Tiempo", justify="right", style="dim")
        table.add_column("Estado", justify="center")

        for r in results:
            p, s = r["product"], r["search"]
            status = "[bold green]✔ OK[/bold green]" if r["overall_ok"] else "[bold red]✘ FALLO[/bold red]"
            stock = p.get("stock", "") or "-"
            price = f"Q {p['price']:,.2f}" if p["ok"] else "-"
            product_name = (p.get("name", "") or "-")[:40] + ("..." if len(p.get("name", "")) > 40 else "")
            search_status = f"{s['results']} resultados" if s["ok"] else "[red]✘[/red]"
            latency = f"{p.get('latency_s', 0):.1f}s / {s.get('latency_s', 0):.1f}s"
            table.add_row(r["store_name"], product_name, price, stock, search_status, latency, status)

        console.print(table)

        failed = [r["store_name"] for r in results if not r["overall_ok"]]
        if failed:
            console.print(f"\n[bold red]⚠️ Tiendas con problemas: {', '.join(failed)}[/bold red]")
            for r in results:
                if r["overall_ok"]:
                    continue
                p_err = r["product"].get("error") or "OK"
                s_err = r["search"].get("error") or "OK"
                console.print(f"  • [bold]{r['store_name']}[/bold]: producto → {p_err} | búsqueda → {s_err}")
        else:
            console.print("\n[bold green]✔ Todas las tiendas respondieron correctamente.[/bold green]")
    except ImportError:  # pragma: no cover - rich siempre está instalado
        for r in results:
            status = "OK" if r["overall_ok"] else "FALLO"
            print(f"{r['store_name']}: {status} | producto={r['product']['ok']} | búsqueda={r['search']['ok']}")
