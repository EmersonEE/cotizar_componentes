import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from src.models import Product
from src.scrapers.base import BaseScraper, ScraperError, ProductNotFoundError

class LaElectronicaScraper(BaseScraper):
    STORE_NAME = "La Electrónica"
    DOMAINS = ["laelectronica.com.gt", "www.laelectronica.com.gt"]

    def can_handle(self, url: str) -> bool:
        parsed = urlparse(url)
        return any(parsed.netloc.lower() == d for d in self.DOMAINS)

    def scrape(self, url: str) -> Product:
        # Normalize URL (strip query params and hashes)
        parsed = urlparse(url)
        clean_path = parsed.path.rstrip('/')
        
        # Primary strategy: Shopify JSON endpoint
        json_url = f"{parsed.scheme}://{parsed.netloc}{clean_path}.json"
        try:
            resp = self.fetch_url(json_url)
            data = resp.json().get("product")
            if data:
                title = data.get("title", "").strip()
                variants = data.get("variants", [])
                if variants:
                    price_val = float(variants[0].get("price", 0.0))
                    is_available = bool(variants[0].get("available", True))
                    sku = variants[0].get("sku")
                else:
                    price_val = 0.0
                    is_available = True
                    sku = None

                images = data.get("images", [])
                image_url = images[0].get("src") if images else None
                if not image_url and data.get("image"):
                    image_url = data.get("image", {}).get("src")

                return Product(
                    name=title,
                    url=url,
                    store_name=self.STORE_NAME,
                    unit_price=round(price_val, 2),
                    currency="GTQ",
                    in_stock=is_available,
                    stock_status="Disponible" if is_available else "Agotado",
                    image_url=image_url,
                    sku=sku
                )
        except Exception:
            # Fallback to HTML parsing if JSON endpoint fails
            pass

        # Fallback strategy: HTML Parsing
        resp = self.fetch_url(url)
        soup = BeautifulSoup(resp.text, "html.parser")

        # 1. Product Title
        title = None
        title_el = soup.select_one("h1.product__title, h1.product-title, .product__title h1, h1")
        if title_el:
            title = title_el.get_text(strip=True)
        if not title:
            og_title = soup.select_one("meta[property='og:title']")
            if og_title and og_title.get("content"):
                title = og_title["content"].strip()
        
        if not title:
            raise ScraperError("No se pudo extraer el nombre del producto en La Electrónica")

        # 2. Price
        price_val = None
        # Meta og:price:amount
        og_price = soup.select_one("meta[property='og:price:amount']")
        if og_price and og_price.get("content"):
            try:
                price_val = self.clean_price(og_price["content"])
            except Exception:
                pass

        if price_val is None:
            price_el = soup.select_one(".price-item--regular, .price-item--sale, .price__regular .price-item, span.price")
            if price_el:
                try:
                    price_val = self.clean_price(price_el.get_text(strip=True))
                except Exception:
                    pass

        if price_val is None:
            raise ScraperError(f"No se pudo extraer el precio del producto: {title}")

        # 3. Stock
        is_available = True
        sold_out_badge = soup.select_one(".price--sold-out, .badge--sold-out, button[disabled][name='add']")
        if sold_out_badge:
            is_available = False

        # 4. Image
        image_url = None
        og_image = soup.select_one("meta[property='og:image']")
        if og_image and og_image.get("content"):
            image_url = og_image["content"]

        return Product(
            name=title,
            url=url,
            store_name=self.STORE_NAME,
            unit_price=price_val,
            currency="GTQ",
            in_stock=is_available,
            stock_status="Disponible" if is_available else "Agotado",
            image_url=image_url
        )
