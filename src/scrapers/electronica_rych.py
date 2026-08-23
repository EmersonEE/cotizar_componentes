import json
import re
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from src.models import Product
from src.scrapers.base import BaseScraper, ScraperError, ProductNotFoundError

class ElectronicaRyCHScraper(BaseScraper):
    STORE_NAME = "Electrónica RyCH"
    DOMAINS = ["electronicarych.com", "www.electronicarych.com"]

    def can_handle(self, url: str) -> bool:
        parsed = urlparse(url)
        return any(parsed.netloc.lower() == d for d in self.DOMAINS)

    def scrape(self, url: str) -> Product:
        resp = self.fetch_url(url)
        soup = BeautifulSoup(resp.text, "html.parser")

        title = None
        price_val = None
        image_url = None
        is_available = True

        # Strategy 1: data-product-tracking-info attribute (Odoo)
        tracking_section = soup.select_one("#product_detail[data-product-tracking-info]")
        if tracking_section:
            raw_info = tracking_section.get("data-product-tracking-info")
            if raw_info:
                try:
                    info = json.loads(raw_info)
                    title = info.get("item_name")
                    if "price" in info:
                        price_val = float(info["price"])
                except Exception:
                    pass

        # Strategy 2: Schema.org metadata
        if not title:
            title_el = soup.select_one("h1[itemprop='name'], h1.o_product_page_title, #product_details h1, h1")
            if title_el:
                title = title_el.get_text(strip=True)

        if not title:
            og_title = soup.select_one("meta[property='og:title']")
            if og_title and og_title.get("content"):
                title = og_title["content"].strip()

        if not title:
            raise ScraperError("No se pudo extraer el nombre del producto en Electrónica RyCH")

        if price_val is None:
            # Try itemprop="price"
            price_meta = soup.select_one("[itemprop='price']")
            if price_meta:
                content = price_meta.get("content") or price_meta.get_text(strip=True)
                try:
                    price_val = self.clean_price(content)
                except Exception:
                    pass

        if price_val is None:
            # Try oe_currency_value
            curr_val = soup.select_one(".oe_currency_value, span.oe_price")
            if curr_val:
                try:
                    price_val = self.clean_price(curr_val.get_text(strip=True))
                except Exception:
                    pass

        if price_val is None:
            raise ScraperError(f"No se pudo extraer el precio del producto: {title}")

        # Image extraction
        img_el = soup.select_one("span[itemprop='image'], img[itemprop='image'], #product_detail img")
        if img_el:
            img_src = img_el.get_text(strip=True) if img_el.name == "span" else img_el.get("src")
            if img_src:
                image_url = urljoin(url, img_src)

        if not image_url:
            og_image = soup.select_one("meta[property='og:image']")
            if og_image and og_image.get("content"):
                image_url = urljoin(url, og_image["content"])

        # Stock / Availability
        avail_el = soup.select_one("link[itemprop='availability']")
        if avail_el and "OutOfStock" in avail_el.get("href", ""):
            is_available = False
        
        # Check out-of-stock warning message in Odoo
        stock_warning = soup.select_one(".alert-warning, .out_of_stock, #out_of_stock_message")
        if stock_warning and any(w in stock_warning.get_text().lower() for w in ["agotado", "sin stock", "out of stock"]):
            is_available = False

        return Product(
            name=title,
            url=url,
            store_name=self.STORE_NAME,
            unit_price=round(price_val, 2),
            currency="GTQ",
            in_stock=is_available,
            stock_status="Disponible" if is_available else "Agotado",
            image_url=image_url
        )
