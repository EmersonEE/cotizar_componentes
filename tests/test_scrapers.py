import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.scrapers import scrape_product, get_scraper_for_url, StoreNotSupportedError

def test_all_scrapers():
    test_urls = [
        "https://laelectronica.com.gt/products/multimetro-zoyi-zt-225-auto-rango-ultra-preciso",
        "https://www.electronicadiy.com/products/fnirsi-hrm-10",
        "https://electronicarych.com/shop/al-22vi-al-22vi-alambre-1mt-calibre-22-violeta-estanado-7381"
    ]

    print("--- INICIANDO TEST DE SCRAPERS ---")
    for url in test_urls:
        print(f"\nProbando URL: {url}")
        try:
            prod = scrape_product(url)
            print(f"  [OK] Tienda: {prod.store_name}")
            print(f"  [OK] Nombre: {prod.name}")
            print(f"  [OK] Precio: {prod.currency} {prod.unit_price:.2f}")
            print(f"  [OK] Stock:  {prod.stock_status}")
            print(f"  [OK] Imagen: {prod.image_url}")
            assert prod.unit_price > 0, "El precio debe ser mayor a 0"
            assert len(prod.name) > 0, "El nombre no puede estar vacío"
        except Exception as e:
            print(f"  [ERROR] Falló extracción: {e}")
            raise e

    # Test unsupported store
    try:
        scrape_product("https://amazon.com/dp/B08N5WRWNW")
        assert False, "Debería fallar para tiendas no soportadas"
    except StoreNotSupportedError:
        print("\n  [OK] Rechazo exitoso de dominio no soportado.")

    print("\n--- TODOS LOS TESTS DE SCRAPING PASARON CON ÉXITO ---")

if __name__ == "__main__":
    test_all_scrapers()
