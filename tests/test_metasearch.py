import sys
import os
import time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.scrapers import metasearch, scrape_product

def test_metasearch_pipeline():
    print("--- INICIANDO TEST DE METABUSCADOR MULTITIENDA ---")

    # Test 1: Search for ESP32
    print("\n🔍 Buscando 'ESP32' en las 3 tiendas...")
    t0 = time.time()
    results_esp = metasearch("ESP32", max_per_store=4)
    elapsed = time.time() - t0
    print(f"  [OK] Tiempo de búsqueda paralela: {elapsed:.2f}s | Resultados encontrados: {len(results_esp)}")
    
    assert len(results_esp) > 0, "Debe encontrar resultados para ESP32"
    stores_found = {r.store_name for r in results_esp}
    print(f"  [OK] Tiendas con resultados para ESP32: {stores_found}")

    for idx, r in enumerate(results_esp[:6], 1):
        print(f"   [{idx}] {r.store_name:<18} | {r.title[:40]:<40} | Q {r.unit_price:>6.2f} | {r.stock_status}")

    # Test 2: Search for Multi-variant component 'resistencia 220'
    print("\n🔍 Buscando 'resistencia 220' (probando desempaquetado de variantes)...")
    t0 = time.time()
    results_res = metasearch("resistencia 220", max_per_store=4)
    elapsed = time.time() - t0
    print(f"  [OK] Tiempo de búsqueda paralela: {elapsed:.2f}s | Resultados encontrados: {len(results_res)}")

    diy_variant_results = [r for r in results_res if r.store_name == "Electrónica DIY"]
    print(f"  [OK] Resultados en DIY para resistencia 220: {len(diy_variant_results)}")
    
    for idx, r in enumerate(results_res[:8], 1):
        print(f"   [{idx}] {r.store_name:<18} | {r.title[:45]:<45} | Q {r.unit_price:>6.2f} | {r.stock_status}")
        if "variant=" in r.url:
            print(f"        ↳ URL con variante: {r.url}")

    # Test 3: Verify that a selected search result resolves cleanly into a Product model
    first_item = results_esp[0]
    print(f"\n📦 Verificando resolución de producto para: {first_item.title} ({first_item.url})")
    product = scrape_product(first_item.url)
    print(f"  [OK] Nombre resuelto: {product.name}")
    print(f"  [OK] Precio resuelto: {product.currency} {product.unit_price:.2f}")
    assert product.unit_price > 0

    print("\n--- TODOS LOS TESTS DE METABÚSQUEDA PASARON CON ÉXITO ---")

if __name__ == "__main__":
    test_metasearch_pipeline()
