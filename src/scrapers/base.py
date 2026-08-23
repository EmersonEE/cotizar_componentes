import re
import time
from abc import ABC, abstractmethod
from typing import Optional
import httpx
from bs4 import BeautifulSoup
from src.models import Product

class ScraperError(Exception):
    """Base exception for scraping errors."""
    pass

class ProductNotFoundError(ScraperError):
    """Raised when product page is not found or out of stock."""
    pass

class StoreNotSupportedError(ScraperError):
    """Raised when a URL is not from a supported store."""
    pass

class BaseScraper(ABC):
    STORE_NAME: str = "Desconocida"
    DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "es-GT,es-ES;q=0.9,es;q=0.8,en;q=0.5",
    }

    def __init__(self, timeout: float = 12.0, max_retries: int = 2):
        self.timeout = timeout
        self.max_retries = max_retries

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """Returns True if this scraper can handle the given URL."""
        pass

    @abstractmethod
    def scrape(self, url: str) -> Product:
        """Scrapes the product from the given URL and returns a Product model."""
        pass

    def fetch_url(self, url: str, headers: Optional[dict] = None, follow_redirects: bool = True) -> httpx.Response:
        """Performs an HTTP GET request with retries and timeout."""
        req_headers = self.DEFAULT_HEADERS.copy()
        if headers:
            req_headers.update(headers)

        last_err = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout, follow_redirects=follow_redirects, verify=True) as client:
                    response = client.get(url, headers=req_headers)
                    if response.status_code == 404:
                        raise ProductNotFoundError(f"Producto no encontrado (HTTP 404): {url}")
                    response.raise_for_status()
                    return response
            except httpx.HTTPStatusError as e:
                last_err = e
                if e.response.status_code == 404:
                    raise ProductNotFoundError(f"Producto no encontrado (HTTP 404): {url}")
                time.sleep(0.5 * attempt)
            except Exception as e:
                last_err = e
                time.sleep(0.5 * attempt)

        raise ScraperError(f"Error al conectar con {url}: {last_err}")

    @staticmethod
    def clean_price(price_str: str) -> float:
        """Sanitizes price strings like 'Q 1,250.00', '1.250,00 Q', 'Q12.50' into a float."""
        if not price_str:
            raise ValueError("Cadena de precio vacía")
        
        # Remove currency symbols and non-numeric chars except commas and dots
        clean = re.sub(r'[^\d.,]', '', price_str.strip())
        if not clean:
            raise ValueError(f"No se pudo extraer valor numérico de: {price_str}")

        # If both comma and dot exist, determine which is thousands and which is decimal
        if ',' in clean and '.' in clean:
            if clean.find(',') < clean.find('.'):
                # 1,234.56 format
                clean = clean.replace(',', '')
            else:
                # 1.234,56 format
                clean = clean.replace('.', '').replace(',', '.')
        elif ',' in clean:
            # Check if it has 2 decimal digits at end (e.g. 12,50)
            parts = clean.split(',')
            if len(parts) == 2 and len(parts[1]) <= 2:
                clean = clean.replace(',', '.')
            else:
                clean = clean.replace(',', '')

        return round(float(clean), 2)
