from typing import List
from src.models import Product
from src.stores import STORES
from src.scrapers.base import BaseScraper, ScraperError, StoreNotSupportedError, ProductNotFoundError
from src.scrapers.la_electronica import LaElectronicaScraper
from src.scrapers.electronica_diy import ElectronicaDIYScraper
from src.scrapers.electronica_rych import ElectronicaRyCHScraper
from src.scrapers.electronica_sigma import ElectronicaSigmaScraper
from src.scrapers.search import (
    SearchResultItem,
    metasearch,
    search_electronica_rych,
    search_la_electronica,
    search_electronica_diy,
    search_electronica_sigma,
)

AVAILABLE_SCRAPERS: List[BaseScraper] = [
    LaElectronicaScraper(),
    ElectronicaDIYScraper(),
    ElectronicaRyCHScraper(),
    ElectronicaSigmaScraper(),
]

# Derivado del registro central de tiendas (T10): dominio principal + alias
SUPPORTED_DOMAINS = [
    domain
    for store in STORES
    for domain in (store.domain, *store.domain_aliases)
]

def get_scraper_for_url(url: str) -> BaseScraper:
    """Finds the appropriate scraper instance for a given URL."""
    for scraper in AVAILABLE_SCRAPERS:
        if scraper.can_handle(url):
            return scraper
    raise StoreNotSupportedError(
        f"La URL '{url}' no pertenece a ninguna de las tiendas soportadas ({', '.join(SUPPORTED_DOMAINS)})."
    )

def scrape_product(url: str) -> Product:
    """Detects store, scrapes the product, and returns a Product model."""
    url = url.strip()
    scraper = get_scraper_for_url(url)
    return scraper.scrape(url)

__all__ = [
    "BaseScraper",
    "ScraperError",
    "StoreNotSupportedError",
    "ProductNotFoundError",
    "LaElectronicaScraper",
    "ElectronicaDIYScraper",
    "ElectronicaRyCHScraper",
    "ElectronicaSigmaScraper",
    "get_scraper_for_url",
    "scrape_product",
    "SUPPORTED_DOMAINS",
    "SearchResultItem",
    "metasearch",
    "search_electronica_rych",
    "search_la_electronica",
    "search_electronica_diy",
    "search_electronica_sigma",
]
