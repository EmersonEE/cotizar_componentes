import logging
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup
from src.models import Product
from src.scrapers.base import BaseScraper, ScraperError

logger = logging.getLogger(__name__)

class LaElectronicaScraper(BaseScraper):
    STORE_NAME = "La Electrónica"
    DOMAINS = ["laelectronica.com.gt", "www.laelectronica.com.gt"]

    def can_handle(self, url: str) -> bool:
        parsed = urlparse(url)
        return any(parsed.netloc.lower() == d for d in self.DOMAINS)

    def scrape(self, url: str) -> Product:
        parsed = urlparse(url)
        clean_path = parsed.path.rstrip('/')
        query_params = parse_qs(parsed.query)
        target_variant_id = query_params.get("variant", [None])[0]
        
        # Primary strategy: Shopify JSON endpoint
        json_url = f"{parsed.scheme}://{parsed.netloc}{clean_path}.json"
        try:
            resp = self.fetch_url(json_url)
            data = resp.json().get("product")
            if data:
                title = data.get("title", "").strip()
                variants = data.get("variants", [])
                
                selected_variant = None
                if target_variant_id and variants:
                    for v in variants:
                        if str(v.get("id")) == str(target_variant_id):
                            selected_variant = v
                            break

                if not selected_variant and variants:
                    selected_variant = variants[0]

                if selected_variant:
                    price_val = float(selected_variant.get("price", 0.0))
                    is_available = bool(selected_variant.get("available", True))
                    sku = selected_variant.get("sku")

                    v_title = selected_variant.get("title", "").strip()
                    if v_title and v_title.lower() != "default title" and v_title not in title:
                        title = f"{title} ({v_title})"
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
        except Exception as e:
            logger.debug("Endpoint JSON de La Electrónica falló para %s (%s); usando fallback HTML.", url, e)
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
