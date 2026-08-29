import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.scrapers import get_scraper_for_url, scrape_product, StoreNotSupportedError
from src.scrapers.base import BaseScraper
from src.scrapers.la_electronica import LaElectronicaScraper
from src.scrapers.electronica_diy import ElectronicaDIYScraper
from src.scrapers.electronica_rych import ElectronicaRyCHScraper
from src.scrapers.electronica_sigma import ElectronicaSigmaScraper


class FakeResponse:
    """Respuesta HTTP falsa con .json() y .text para simular fetch_url."""

    def __init__(self, data=None, text=""):
        self._data = data
        self.text = text

    def json(self):
        return self._data


class TestScrapersOffline(unittest.TestCase):

    def test_can_handle_domains(self):
        self.assertTrue(LaElectronicaScraper().can_handle("https://laelectronica.com.gt/products/x"))
        self.assertTrue(LaElectronicaScraper().can_handle("https://www.laelectronica.com.gt/products/x"))
        self.assertFalse(LaElectronicaScraper().can_handle("https://electronicarych.com/shop/x"))

        self.assertTrue(ElectronicaDIYScraper().can_handle("https://www.electronicadiy.com/products/x"))
        self.assertFalse(ElectronicaDIYScraper().can_handle("https://laelectronica.com.gt/products/x"))

        self.assertTrue(ElectronicaRyCHScraper().can_handle("https://electronicarych.com/shop/x"))
        self.assertFalse(ElectronicaRyCHScraper().can_handle("https://example.com/x"))

    def test_get_scraper_for_url_and_unsupported(self):
        self.assertIsInstance(get_scraper_for_url("https://laelectronica.com.gt/products/x"), LaElectronicaScraper)
        self.assertIsInstance(get_scraper_for_url("https://electronicarych.com/shop/x"), ElectronicaRyCHScraper)
        with self.assertRaises(StoreNotSupportedError):
            get_scraper_for_url("https://amazon.com/dp/B08N5WRWNW")

    def test_clean_price_variants(self):
        clean_price = BaseScraper.clean_price
        self.assertEqual(clean_price("Q 1,250.00"), 1250.0)
        self.assertEqual(clean_price("1.250,00 Q"), 1250.0)
        self.assertEqual(clean_price("Q12.50"), 12.5)
        self.assertEqual(clean_price("Q 85"), 85.0)
        self.assertEqual(clean_price("12,50"), 12.5)
        with self.assertRaises(ValueError):
            clean_price("")

    def test_la_electronica_json_path(self):
        scraper = LaElectronicaScraper()
        fake = FakeResponse(data={
            "product": {
                "title": "ESP32 DevKit",
                "variants": [{"id": "123", "price": "95.00", "available": True,
                              "title": "Default Title", "sku": "ESP32-1"}],
                "images": [{"src": "https://x/img.jpg"}],
            }
        })
        with patch.object(scraper, "fetch_url", return_value=fake):
            prod = scraper.scrape("https://laelectronica.com.gt/products/esp32")
        self.assertEqual(prod.name, "ESP32 DevKit")
        self.assertEqual(prod.unit_price, 95.0)
        self.assertTrue(prod.in_stock)
        self.assertEqual(prod.sku, "ESP32-1")

    def test_la_electronica_variant_selection(self):
        scraper = LaElectronicaScraper()
        variants = [
            {"id": "1", "price": "10.00", "available": True, "title": "1/4W", "sku": "R1"},
            {"id": "2", "price": "20.00", "available": False, "title": "1/2W", "sku": "R2"},
        ]
        fake = FakeResponse(data={"product": {"title": "Resistencia", "variants": variants, "images": []}})
        with patch.object(scraper, "fetch_url", return_value=fake):
            prod = scraper.scrape("https://laelectronica.com.gt/products/resistencia?variant=2")
        self.assertEqual(prod.unit_price, 20.0)
        self.assertFalse(prod.in_stock)
        self.assertIn("1/2W", prod.name)

    def test_la_electronica_html_fallback(self):
        scraper = LaElectronicaScraper()
        html = ("<html><head><meta property='og:title' content='Multimetro Digital'>"
                "<meta property='og:price:amount' content='45.00'></head><body></body></html>")
        # 1er fetch (JSON) falla; 2do fetch (HTML) devuelve la página
        with patch.object(scraper, "fetch_url", side_effect=[
            RuntimeError("JSON endpoint caído"),
            FakeResponse(text=html),
        ]):
            prod = scraper.scrape("https://laelectronica.com.gt/products/multimetro")
        self.assertEqual(prod.name, "Multimetro Digital")
        self.assertEqual(prod.unit_price, 45.0)

    def test_rych_schema_org_title(self):
        scraper = ElectronicaRyCHScraper()
        html = ("<html><head><meta property='og:title' content='ESP32 Modulo WiFi'>"
                "<meta property='og:image' content='/img/esp32.jpg'></head>"
                "<body><span class='oe_currency_value'>99.50</span></body></html>")
        with patch.object(scraper, "fetch_url", return_value=FakeResponse(text=html)):
            prod = scraper.scrape("https://electronicarych.com/shop/esp32")
        self.assertEqual(prod.name, "ESP32 Modulo WiFi")
        self.assertEqual(prod.unit_price, 99.5)

    def test_scrape_product_dispatches_by_url(self):
        with patch.object(LaElectronicaScraper, "fetch_url",
                          return_value=FakeResponse(data={"product": {"title": "X", "variants": [], "images": []}})):
            prod = scrape_product("https://laelectronica.com.gt/products/x")
        self.assertEqual(prod.store_name, "La Electrónica")
        with self.assertRaises(StoreNotSupportedError):
            scrape_product("https://amazon.com/dp/B08N5WRWNW")


class TestElectronicaSigmaScraper(unittest.TestCase):

    SIGMA_JSONLD = (
        "<html><head></head><body><script type='application/ld+json'>"
        '{"@context":"https://schema.org/","@graph":['
        '{"@type":"BreadcrumbList","itemListElement":[]},'
        '{"@type":"Product","name":"MODULO WIFI + BLUETOOTH ESP32 38 PINES",'
        '"image":"https://sigma/img.png","sku":"CA54/CP96-5",'
        '"offers":[{"@type":"Offer","price":"125.00","priceCurrency":"GTQ",'
        '"availability":"https://schema.org/InStock"}]}]}'
        "</script></body></html>"
    )

    def test_can_handle_domains(self):
        self.assertTrue(ElectronicaSigmaScraper().can_handle("https://electronicasigma.com.gt/producto/x"))
        self.assertTrue(ElectronicaSigmaScraper().can_handle("https://www.electronicasigma.com.gt/producto/x"))
        self.assertFalse(ElectronicaSigmaScraper().can_handle("https://laelectronica.com.gt/products/x"))

    def test_jsonld_product_path(self):
        scraper = ElectronicaSigmaScraper()
        with patch.object(scraper, "fetch_url", return_value=FakeResponse(text=self.SIGMA_JSONLD)):
            prod = scraper.scrape("https://electronicasigma.com.gt/producto/modulo-wifi-bluetooth-esp32-38-pines/")
        self.assertEqual(prod.name, "MODULO WIFI + BLUETOOTH ESP32 38 PINES")
        self.assertEqual(prod.unit_price, 125.0)
        self.assertEqual(prod.sku, "CA54/CP96-5")
        self.assertTrue(prod.in_stock)
        self.assertEqual(prod.image_url, "https://sigma/img.png")

    def test_jsonld_out_of_stock(self):
        html = self.SIGMA_JSONLD.replace("InStock", "OutOfStock")
        scraper = ElectronicaSigmaScraper()
        with patch.object(scraper, "fetch_url", return_value=FakeResponse(text=html)):
            prod = scraper.scrape("https://electronicasigma.com.gt/producto/x")
        self.assertFalse(prod.in_stock)
        self.assertEqual(prod.stock_status, "Agotado")

    def test_html_fallback_without_jsonld(self):
        html = ("<html><body><h1 class='product_title'>Cautín 60W</h1>"
                "<div class='summary'><p class='price'><span class='woocommerce-Price-amount amount'>"
                "<bdi><span>Q</span> 45.00</bdi></span></p></div></body></html>")
        scraper = ElectronicaSigmaScraper()
        with patch.object(scraper, "fetch_url", return_value=FakeResponse(text=html)):
            prod = scraper.scrape("https://electronicasigma.com.gt/producto/cautin-60w/")
        self.assertEqual(prod.name, "Cautín 60W")
        self.assertEqual(prod.unit_price, 45.0)

    def test_get_scraper_for_url_sigma(self):
        self.assertIsInstance(get_scraper_for_url("https://electronicasigma.com.gt/producto/x"), ElectronicaSigmaScraper)


if __name__ == "__main__":
    unittest.main()
