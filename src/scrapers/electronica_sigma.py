"""Scraper para Electrónica Sigma (https://electronicasigma.com.gt/) — WooCommerce.

Producto: JSON-LD (@graph -> Product) con nombre, precio GTQ, SKU, imagen y
disponibilidad; fallback a parseo HTML (h1 + .price + clases de stock).
"""
import json
import re
import logging
from typing import Optional, Dict, Any
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from src.models import Product
from src.scrapers.base import BaseScraper, ScraperError

logger = logging.getLogger(__name__)

# Primer token tipo precio (maneja 'Q 125.00', 'Q 1,250.00', 'Q 10.00 – Q 20.00')
_PRICE_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)*")

_OUT_OF_STOCK_WORDS = ("agotado", "sin existencias", "sin stock", "out of stock", "no disponible")


def _extract_price_text(text: str) -> Optional[float]:
    """Extrae el primer precio válido de un texto (tarjeta o página de producto)."""
    if not text or not text.strip():
        return None
    m = _PRICE_TOKEN_RE.search(text)
    if not m:
        return None
    try:
        return round(BaseScraper.clean_price(m.group(0)), 2)
    except Exception:
        return None


def _extract_product_jsonld(soup: BeautifulSoup) -> Optional[Dict[str, Any]]:
    """Busca el nodo Product en los bloques JSON-LD (@graph o raíz)."""
    for script in soup.select("script[type='application/ld+json']"):
        raw = script.string
        if not raw:
            continue
        try:
            data = json.loads(raw.strip())
        except Exception:
            continue
        candidates = []
        if isinstance(data, dict):
            if data.get("@type") == "Product":
                candidates.append(data)
            graph = data.get("@graph")
            if isinstance(graph, list):
                candidates.extend(n for n in graph if isinstance(n, dict) and n.get("@type") == "Product")
        for node in candidates:
            if node.get("name"):
                return node
    return None


def _parse_offer_price(offer: Dict[str, Any]) -> Optional[float]:
    """Precio de una oferta WooCommerce: 'price' o priceSpecification[].price."""
    raw_price = offer.get("price")
    if raw_price is None:
        spec = offer.get("priceSpecification")
        if isinstance(spec, dict):
            raw_price = spec.get("price")
        elif isinstance(spec, list) and spec:
            raw_price = spec[0].get("price")
    if raw_price is None:
        return None
    try:
        return float(str(raw_price).replace(",", ""))
    except (TypeError, ValueError):
        return None


class ElectronicaSigmaScraper(BaseScraper):
    STORE_NAME = "Electrónica Sigma"
    DOMAINS = ["electronicasigma.com.gt", "www.electronicasigma.com.gt"]

    def can_handle(self, url: str) -> bool:
        parsed = urlparse(url)
        return any(parsed.netloc.lower() == d for d in self.DOMAINS)

    def scrape(self, url: str) -> Product:
        resp = self.fetch_url(url)
        soup = BeautifulSoup(resp.text, "html.parser")

        # Estrategia principal: JSON-LD (WooCommerce)
        node = _extract_product_jsonld(soup)
        if node:
            offers = node.get("offers") or []
            offer = offers[0] if isinstance(offers, list) and offers else ({})
            price_val = _parse_offer_price(offer) if isinstance(offer, dict) else None
            availability = str((offer.get("availability") if isinstance(offer, dict) else "") or "")
            is_available = "OutOfStock" not in availability
            image_url = node.get("image")
            if isinstance(image_url, list):
                image_url = image_url[0] if image_url else None

            if price_val is not None and price_val > 0:
                return Product(
                    name=node.get("name", "").strip(),
                    url=url,
                    store_name=self.STORE_NAME,
                    unit_price=round(price_val, 2),
                    currency="GTQ",
                    in_stock=is_available,
                    stock_status="Disponible" if is_available else "Agotado",
                    image_url=image_url,
                    sku=node.get("sku"),
                )
            logger.debug("Sigma: JSON-LD sin precio válido para %s; usando fallback HTML.", url)

        # Fallback: HTML
        title = None
        title_el = soup.select_one("h1.product_title, h1.entry-title, h1")
        if title_el:
            title = title_el.get_text(strip=True)
        if not title:
            og_title = soup.select_one("meta[property='og:title']")
            if og_title and og_title.get("content"):
                title = og_title["content"].strip()
        if not title:
            raise ScraperError("No se pudo extraer el nombre del producto en Electrónica Sigma")

        price_val = None
        price_el = soup.select_one(".summary .price, .entry-summary .price, .price")
        if price_el:
            price_val = _extract_price_text(price_el.get_text(" ", strip=True))
        if price_val is None or price_val <= 0:
            raise ScraperError(f"No se pudo extraer el precio del producto: {title}")

        is_available = True
        stock_el = soup.select_one(".stock, .woo-custom-stock-status")
        if stock_el:
            txt = stock_el.get_text(" ", strip=True).lower()
            if any(w in txt for w in _OUT_OF_STOCK_WORDS):
                is_available = False

        image_url = None
        og_image = soup.select_one("meta[property='og:image']")
        if og_image and og_image.get("content"):
            image_url = og_image["content"]

        return Product(
            name=title,
            url=url,
            store_name=self.STORE_NAME,
            unit_price=round(price_val, 2),
            currency="GTQ",
            in_stock=is_available,
            stock_status="Disponible" if is_available else "Agotado",
            image_url=image_url,
        )
