import re
import unicodedata
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Optional, Set
from bs4 import BeautifulSoup

from src.scrapers.base import BaseScraper

@dataclass
class SearchResultItem:
    store_name: str
    title: str
    url: str
    unit_price: float
    in_stock: bool
    stock_status: str
    image_url: Optional[str] = None

    def __repr__(self) -> str:
        return f"<SearchResultItem {self.store_name} | {self.title} | Q{self.unit_price:.2f} | {self.stock_status}>"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-GT,es-ES;q=0.9,es;q=0.8,en;q=0.5",
}

def robust_fetch_html(url: str, timeout: float = 6.0) -> str:
    """Fetches raw HTML with resilient headers avoiding Cloudflare/Shopify 429 blocks."""
    req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', errors='ignore')
    except Exception:
        return ""

def clean_search_term_tiers(raw_query: str) -> List[str]:
    """Generates tiered query variations from full query to core electronic keywords."""
    if not raw_query or not raw_query.strip():
        return []

    # 1. Normalize accents
    nfkd = unicodedata.normalize('NFKD', raw_query)
    text = ''.join([c for c in nfkd if not unicodedata.combining(c)]).lower()
    
    # 2. Strip leading quantity (e.g. '2x ', '50 ')
    text = re.sub(r'^(?:\d+\s*[xX×\*\-]\s*|\d+\s+)', '', text).strip()
    
    # 3. Strip quotes and punctuation
    text = re.sub(r'[\"\'\`\,\;\:\(\)\[\]]', ' ', text)
    
    # 4. Filter stop words & conversational noise
    stopwords = r'\b(?:de|con|para|un|una|unos|unas|pulgadas?|pulg|inch|grande|grandes|chico|chicos|conexion|conexiones|puerto|puertos|azules?|rojos?|verdes?|amarillos?|blancos?|negros?|pantallas?|displays?|sensores?|modulos?|cables?|metros?)\b'
    filtered = re.sub(stopwords, ' ', text, flags=re.IGNORECASE)
    filtered = re.sub(r'\s+', ' ', filtered).strip()
    
    tiers = []
    if filtered:
        tiers.append(filtered)
        
    # OLED Screen
    if 'oled' in text:
        if '0.96' in text or '128' in text:
            tiers.insert(0, 'oled 0.96 i2c' if 'i2c' in text else 'oled 0.96')
            tiers.append('oled 0.96')
            tiers.append('display oled')
        tiers.append('oled')
        
    # Ultrasonic sensor
    if 'sr04' in text or 'ultrason' in text:
        tiers.insert(0, 'hc-sr04')
        tiers.append('sensor ultrasonico')
        
    # Bluetooth HC-05 / HC-06
    if 'hc' in text and '05' in text:
        tiers.insert(0, 'hc-05')
        tiers.append('bluetooth hc-05')

    # Servo SG90
    if 'sg90' in text or 'servo' in text:
        tiers.insert(0, 'sg90')
        tiers.append('servomotor sg90')
        
    # Dupont cables
    if 'dupont' in text:
        if 'macho' in text and 'hembra' in text:
            tiers.insert(0, 'dupont macho hembra')
        tiers.append('dupont')

    # Protoboard 830
    if 'protoboard' in text or '830' in text:
        tiers.insert(0, 'protoboard 830')
        tiers.append('protoboard')

    # Power supply 12V 2A
    if '12v' in text and '2a' in text:
        tiers.insert(0, 'fuente 12v 2a')
        tiers.append('fuente 12v')

    # Deduplicate while preserving priority order
    seen = set()
    uniq = []
    for t in tiers:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq

def search_electronica_rych_single_term(query: str, limit: int = 8, timeout: float = 6.0) -> List[SearchResultItem]:
    """Searches Electrónica RyCH for a specific search term."""
    results: List[SearchResultItem] = []
    encoded_query = urllib.parse.quote_plus(query.strip())
    search_url = f"https://electronicarych.com/shop?search={encoded_query}"

    html = robust_fetch_html(search_url, timeout=timeout)
    if not html:
        return results

    soup = BeautifulSoup(html, "html.parser")
    product_elements = soup.select(".oe_product, td.oe_product, div.o_wsale_product_grid_wrapper")
    
    seen_urls: Set[str] = set()
    for p in product_elements:
        if len(results) >= limit:
            break

        link_el = p.select_one("h6.o_wsale_products_item_title a, a[itemprop='name'], h6 a, a.text-primary")
        if not link_el:
            for a in p.select("a[href*='/shop/']"):
                txt = a.get_text(strip=True)
                if txt and len(txt) > 3:
                    link_el = a
                    break

        if not link_el:
            continue

        raw_href = link_el.get("href", "")
        if not raw_href or "/shop/" not in raw_href or "/shop/cart" in raw_href or "/shop/category" in raw_href:
            continue

        full_url = urllib.parse.urljoin("https://electronicarych.com", raw_href)
        clean_url = full_url.split("?")[0]
        if clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)

        title = link_el.get_text(strip=True)
        if not title:
            continue

        price_el = p.select_one(".oe_currency_value, span[itemprop='price'], .oe_price, .product_price")
        price_val = 0.0
        if price_el:
            try:
                nums = re.findall(r"(\d+(?:\.\d+)?)", price_el.get_text())
                if nums:
                    price_val = float(nums[0])
            except Exception:
                pass

        img_el = p.select_one("img[itemprop='image'], img")
        image_url = None
        if img_el and img_el.get("src"):
            image_url = urllib.parse.urljoin("https://electronicarych.com", img_el["src"])

        if price_val > 0:
            results.append(SearchResultItem(
                store_name="Electrónica RyCH",
                title=title,
                url=full_url,
                unit_price=round(price_val, 2),
                in_stock=True,
                stock_status="Disponible",
                image_url=image_url
            ))

    return results

def search_electronica_rych(query: str, limit: int = 8, timeout: float = 6.0) -> List[SearchResultItem]:
    """Cascading search in Electrónica RyCH."""
    tiers = clean_search_term_tiers(query)
    seen_urls: Set[str] = set()
    all_results: List[SearchResultItem] = []

    for t in tiers:
        found = search_electronica_rych_single_term(t, limit=limit, timeout=timeout)
        for item in found:
            if item.url not in seen_urls:
                seen_urls.add(item.url)
                all_results.append(item)
        if len(all_results) >= 2:
            break

    return all_results[:limit]

def search_la_electronica_single_term(query: str, limit: int = 8, timeout: float = 6.0) -> List[SearchResultItem]:
    """Searches La Electrónica for a specific search term."""
    results: List[SearchResultItem] = []
    encoded_query = urllib.parse.quote_plus(query.strip())
    search_url = f"https://laelectronica.com.gt/search?q={encoded_query}&type=product"

    html = robust_fetch_html(search_url, timeout=timeout)
    if not html:
        return results

    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(".card-wrapper, .grid__item, .card")
    
    seen_urls: Set[str] = set()
    for card in cards:
        if len(results) >= limit:
            break

        link_el = card.select_one("h3.card__heading a, .card-information h3 a, a[href*='/products/']")
        if not link_el:
            continue

        raw_href = link_el.get("href", "")
        if not raw_href or "/products/" not in raw_href:
            continue

        full_url = urllib.parse.urljoin("https://laelectronica.com.gt", raw_href)
        clean_url = full_url.split("?")[0]
        if clean_url in seen_urls:
            continue

        title = link_el.get_text(strip=True)
        if not title or title.lower() in ["destacados", "ofertas", "ver más"]:
            continue
        seen_urls.add(clean_url)

        price_val = 0.0
        price_el = card.select_one(".price-item--sale, .price-item--regular, .price")
        if price_el:
            try:
                nums = re.findall(r"(\d+(?:\.\d+)?)", price_el.get_text())
                if nums:
                    price_val = float(nums[0])
            except Exception:
                pass

        sold_out_el = card.select_one(".badge--bottom-left, .sold-out, .price--sold-out")
        is_available = not bool(sold_out_el)

        img_el = card.select_one("img")
        image_url = None
        if img_el and img_el.get("src"):
            image_url = urllib.parse.urljoin("https://laelectronica.com.gt", img_el["src"])

        if price_val > 0:
            results.append(SearchResultItem(
                store_name="La Electrónica",
                title=title,
                url=full_url,
                unit_price=round(price_val, 2),
                in_stock=is_available,
                stock_status="Disponible" if is_available else "Agotado",
                image_url=image_url
            ))

    return results

def search_la_electronica(query: str, limit: int = 8, timeout: float = 6.0) -> List[SearchResultItem]:
    """Cascading search in La Electrónica."""
    tiers = clean_search_term_tiers(query)
    seen_urls: Set[str] = set()
    all_results: List[SearchResultItem] = []

    for t in tiers:
        found = search_la_electronica_single_term(t, limit=limit, timeout=timeout)
        for item in found:
            if item.url not in seen_urls:
                seen_urls.add(item.url)
                all_results.append(item)
        if len(all_results) >= 2:
            break

    return all_results[:limit]

def search_electronica_diy_single_term(query: str, limit: int = 8, timeout: float = 6.0) -> List[SearchResultItem]:
    """Searches Electrónica DIY for a specific search term."""
    results: List[SearchResultItem] = []
    encoded_query = urllib.parse.quote_plus(query.strip())
    search_url = f"https://www.electronicadiy.com/search?q={encoded_query}"

    html = robust_fetch_html(search_url, timeout=timeout)
    if not html:
        return results

    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(".productitem, .grid-view-item, .product-card")
    
    seen_handles: Set[str] = set()
    for card in cards:
        if len(results) >= limit:
            break

        t_el = card.select_one(".productitem--title, h2, h3, .product-card__title")
        link_el = card.select_one("a[href*='/products/']")
        if not t_el or not link_el:
            continue

        raw_href = link_el.get("href", "")
        if "/products/" not in raw_href:
            continue

        title = t_el.get_text(strip=True)
        if not title:
            continue

        full_url = urllib.parse.urljoin("https://www.electronicadiy.com", raw_href)
        clean_url = full_url.split("?")[0]
        if clean_url in seen_handles:
            continue
        seen_handles.add(clean_url)

        price_val = 0.0
        price_el = card.select_one(".price__current, .productitem--price, .price")
        if price_el:
            try:
                nums = re.findall(r"(\d+(?:\.\d+)?)", price_el.get_text())
                if nums:
                    price_val = float(nums[0])
            except Exception:
                pass

        sold_out_el = card.select_one(".badge--soldout, .productitem--badge-soldout, [class*='sold-out']")
        is_available = not bool(sold_out_el)

        img_el = card.select_one("img")
        image_url = None
        if img_el and img_el.get("src"):
            image_url = urllib.parse.urljoin("https://www.electronicadiy.com", img_el["src"])

        if price_val > 0:
            results.append(SearchResultItem(
                store_name="Electrónica DIY",
                title=title,
                url=full_url,
                unit_price=round(price_val, 2),
                in_stock=is_available,
                stock_status="Disponible" if is_available else "Agotado",
                image_url=image_url
            ))

    return results

def search_electronica_diy(query: str, limit: int = 8, timeout: float = 6.0) -> List[SearchResultItem]:
    """Cascading search in Electrónica DIY."""
    tiers = clean_search_term_tiers(query)
    seen_urls: Set[str] = set()
    all_results: List[SearchResultItem] = []

    for t in tiers:
        found = search_electronica_diy_single_term(t, limit=limit, timeout=timeout)
        for item in found:
            if item.url not in seen_urls:
                seen_urls.add(item.url)
                all_results.append(item)
        if len(all_results) >= 2:
            break

    return all_results[:limit]

def metasearch(query: str, max_per_store: int = 5, timeout: float = 6.0) -> List[SearchResultItem]:
    """Executes parallel search across the 3 supported stores."""
    combined_results: List[SearchResultItem] = []
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(search_electronica_rych, query, max_per_store, timeout): "Electrónica RyCH",
            executor.submit(search_la_electronica, query, max_per_store, timeout): "La Electrónica",
            executor.submit(search_electronica_diy, query, max_per_store, timeout): "Electrónica DIY"
        }
        
        for future in as_completed(futures):
            try:
                store_items = future.result()
                combined_results.extend(store_items)
            except Exception:
                pass
                
    return combined_results
