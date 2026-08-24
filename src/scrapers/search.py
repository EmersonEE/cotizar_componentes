import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Optional, Set
import httpx
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

def search_electronica_rych(query: str, limit: int = 6, timeout: float = 6.0) -> List[SearchResultItem]:
    """Searches Electrónica RyCH (Tipo A: one page = one specific product)."""
    results: List[SearchResultItem] = []
    encoded_query = urllib.parse.quote_plus(query.strip())
    search_url = f"https://electronicarych.com/shop?search={encoded_query}"

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(search_url, headers=DEFAULT_HEADERS)
            if resp.status_code != 200:
                return results

            soup = BeautifulSoup(resp.text, "html.parser")
            product_elements = soup.select(".oe_product, td.oe_product, div.o_wsale_product_grid_wrapper")
            
            seen_urls: Set[str] = set()
            for p in product_elements:
                if len(results) >= limit:
                    break

                # Specifically look for title link inside h6 or itemprop
                link_el = p.select_one("h6.o_wsale_products_item_title a, a[itemprop='name'], h6 a, a.text-primary")
                if not link_el:
                    # Fallback to any link containing text
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
                        # Extract first float/decimal from price container
                        nums = re.findall(r"(\d+(?:\.\d+)?)", price_el.get_text())
                        if nums:
                            price_val = float(nums[0])
                        else:
                            price_val = BaseScraper.clean_price(price_el.get_text(strip=True))
                    except Exception:
                        pass

                img_el = p.select_one("img[itemprop='image'], img")
                image_url = None
                if img_el and img_el.get("src"):
                    image_url = urllib.parse.urljoin("https://electronicarych.com", img_el["src"])

                results.append(SearchResultItem(
                    store_name="Electrónica RyCH",
                    title=title,
                    url=full_url,
                    unit_price=round(price_val, 2),
                    in_stock=True,
                    stock_status="Disponible",
                    image_url=image_url
                ))
    except Exception:
        pass

    return results

def search_la_electronica(query: str, limit: int = 6, timeout: float = 6.0) -> List[SearchResultItem]:
    """Searches La Electrónica (Tipo A: one page = one specific product)."""
    results: List[SearchResultItem] = []
    encoded_query = urllib.parse.quote_plus(query.strip())
    search_url = f"https://laelectronica.com.gt/search?q={encoded_query}&type=product"

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(search_url, headers=DEFAULT_HEADERS)
            if resp.status_code != 200:
                return results

            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.select(".card-wrapper, .card--card, .grid__item")
            
            seen_urls: Set[str] = set()
            for card in cards:
                if len(results) >= limit:
                    break

                link_el = card.select_one(".card__heading a, a.full-unstyled-link, .card-information h3 a")
                if not link_el:
                    continue

                raw_href = link_el.get("href", "")
                if not raw_href or "/products/" not in raw_href:
                    continue

                full_url = urllib.parse.urljoin("https://laelectronica.com.gt", raw_href)
                clean_url = full_url.split("?")[0]
                if clean_url in seen_urls:
                    continue
                seen_urls.add(clean_url)

                title = link_el.get_text(strip=True)
                if not title or title.lower() in ["destacados", "ofertas", "ver más"]:
                    continue

                # Extract price
                price_val = 0.0
                price_el = card.select_one(".price-item--sale, .price-item--regular, .price")
                if price_el:
                    try:
                        # Extract first number like 120.00
                        nums = re.findall(r"(\d+(?:\.\d+)?)", price_el.get_text())
                        if nums:
                            price_val = float(nums[0])
                        else:
                            price_val = BaseScraper.clean_price(price_el.get_text(strip=True))
                    except Exception:
                        pass

                sold_out_el = card.select_one(".badge--bottom-left, .sold-out, .price--sold-out")
                is_available = not bool(sold_out_el)

                img_el = card.select_one("img")
                image_url = None
                if img_el and img_el.get("src"):
                    image_url = urllib.parse.urljoin("https://laelectronica.com.gt", img_el["src"])

                results.append(SearchResultItem(
                    store_name="La Electrónica",
                    title=title,
                    url=full_url,
                    unit_price=round(price_val, 2),
                    in_stock=is_available,
                    stock_status="Disponible" if is_available else "Agotado",
                    image_url=image_url
                ))
    except Exception:
        pass

    return results

def search_electronica_diy(query: str, limit: int = 8, timeout: float = 6.0) -> List[SearchResultItem]:
    """
    Searches Electrónica DIY (Tipo B: multiple variants per page).
    Unpacks matching variants into separate, distinct search results.
    """
    results: List[SearchResultItem] = []
    encoded_query = urllib.parse.quote_plus(query.strip())
    search_url = f"https://www.electronicadiy.com/search?q={encoded_query}"

    # Extract search tokens/numbers (e.g. '220', '10k', '100uf', 'esp32')
    tokens = [t.lower() for t in re.split(r'[\s,._-]+', query) if len(t) >= 2]

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(search_url, headers=DEFAULT_HEADERS)
            if resp.status_code != 200:
                return results

            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.select(".productitem, .grid-view-item, .product-card")
            
            seen_handles: Set[str] = set()
            for card in cards:
                if len(results) >= limit:
                    break

                link_el = card.select_one(".productitem--title a, a[href*='/products/']")
                if not link_el:
                    continue

                raw_href = link_el.get("href", "")
                if "/products/" not in raw_href:
                    continue

                full_url = urllib.parse.urljoin("https://www.electronicadiy.com", raw_href)
                parsed = urllib.parse.urlparse(full_url)
                handle = parsed.path.rstrip('/').split('/')[-1]
                query_params = urllib.parse.parse_qs(parsed.query)
                direct_variant_id = query_params.get("variant", [None])[0]

                if handle in seen_handles and not direct_variant_id:
                    continue
                seen_handles.add(handle)

                # Fetch JSON for product to get exact variants
                json_url = f"https://www.electronicadiy.com/products/{handle}.json"
                try:
                    j_resp = client.get(json_url, headers=DEFAULT_HEADERS)
                    if j_resp.status_code == 200:
                        prod_data = j_resp.json().get("product", {})
                        base_title = prod_data.get("title", "").strip()
                        variants = prod_data.get("variants", [])
                        images = prod_data.get("images", [])
                        img_src = images[0].get("src") if images else None

                        if len(variants) > 1:
                            # Multi-variant product: find matching variants
                            matched_any = False
                            for v in variants:
                                v_id = v.get("id")
                                v_title = str(v.get("title", "")).strip()
                                
                                is_direct_match = direct_variant_id and str(v_id) == str(direct_variant_id)
                                matches_token = any(t in v_title.lower() for t in tokens)

                                if is_direct_match or matches_token:
                                    matched_any = True
                                    v_url = f"https://www.electronicadiy.com/products/{handle}?variant={v_id}"
                                    display_title = f"{base_title} ({v_title})" if v_title.lower() != "default title" else base_title
                                    price_val = float(v.get("price", 0.0))
                                    avail = v.get("available")
                                    is_avail = True if avail is None else bool(avail)

                                    results.append(SearchResultItem(
                                        store_name="Electrónica DIY",
                                        title=display_title,
                                        url=v_url,
                                        unit_price=round(price_val, 2),
                                        in_stock=is_avail,
                                        stock_status="Disponible" if is_avail else "Agotado",
                                        image_url=img_src
                                    ))
                                    if len(results) >= limit:
                                        break

                            if not matched_any and variants:
                                # Fallback to first variant if no specific variant matched
                                v0 = variants[0]
                                v_url = f"https://www.electronicadiy.com/products/{handle}?variant={v0.get('id')}"
                                price_val = float(v0.get("price", 0.0))
                                avail = v0.get("available")
                                is_avail = True if avail is None else bool(avail)
                                results.append(SearchResultItem(
                                    store_name="Electrónica DIY",
                                    title=base_title,
                                    url=v_url,
                                    unit_price=round(price_val, 2),
                                    in_stock=is_avail,
                                    stock_status="Disponible" if is_avail else "Agotado",
                                    image_url=img_src
                                ))
                        else:
                            # Single variant product
                            v0 = variants[0] if variants else {}
                            price_val = float(v0.get("price", 0.0)) if v0 else 0.0
                            avail = v0.get("available") if v0 else True
                            is_avail = True if avail is None else bool(avail)

                            results.append(SearchResultItem(
                                store_name="Electrónica DIY",
                                title=base_title,
                                url=f"https://www.electronicadiy.com/products/{handle}",
                                unit_price=round(price_val, 2),
                                in_stock=is_avail,
                                stock_status="Disponible" if is_avail else "Agotado",
                                image_url=img_src
                            ))
                except Exception:
                    # Fallback to HTML card data if JSON failed
                    title_el = card.select_one(".productitem--title a, h2 a, .productitem--title")
                    price_el = card.select_one(".price--main, .money, .price")
                    sold_out_el = card.select_one(".badge--sold-out, .sold-out")
                    title = title_el.get_text(strip=True) if title_el else handle
                    price_val = 0.0
                    if price_el:
                        try:
                            nums = re.findall(r"(\d+(?:\.\d+)?)", price_el.get_text())
                            if nums:
                                price_val = float(nums[0])
                            else:
                                price_val = BaseScraper.clean_price(price_el.get_text(strip=True))
                        except Exception:
                            pass
                    results.append(SearchResultItem(
                        store_name="Electrónica DIY",
                        title=title,
                        url=full_url,
                        unit_price=round(price_val, 2),
                        in_stock=not bool(sold_out_el),
                        stock_status="Disponible" if not bool(sold_out_el) else "Agotado"
                    ))
    except Exception:
        pass

    return results

def metasearch(query: str, max_per_store: int = 5, timeout: float = 6.0) -> List[SearchResultItem]:
    """
    Runs concurrent parallel searches across all 3 Guatemalan stores
    and consolidates the results into a single clean list.
    """
    query = query.strip()
    if not query:
        return []

    combined_results: List[SearchResultItem] = []

    with ThreadPoolExecutor(max_workers=3) as executor:
        future_rych = executor.submit(search_electronica_rych, query, max_per_store, timeout)
        future_la = executor.submit(search_la_electronica, query, max_per_store, timeout)
        future_diy = executor.submit(search_electronica_diy, query, max_per_store * 2, timeout)

        futures = [future_rych, future_la, future_diy]
        for future in as_completed(futures):
            try:
                res = future.result()
                if res:
                    combined_results.extend(res)
            except Exception:
                pass

    return combined_results
