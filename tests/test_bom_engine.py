import sys
import os
import time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.bom_parser import parse_bom_text, parse_bom_line
from src.core.bom_searcher import calculate_match_score, search_bom_items_parallel

def test_bom_parser_formats():
    print("--- 1. PROBANDO PARSER DE TEXTO BOM ---")
    sample_text = """
    2x ESP32 NodeMCU
    10x Resistencia 220 ohm 1/4W
    Sensor de temperatura DHT22
    Modulo Relay 5V 2 canales
    Pantalla OLED 0.96 I2C
    5 pcs Arduino Uno R3
    - 4x LM358
    * 100 × Resistencia 10k 1/4W
    Protoboard 830 puntos
    LED Rojo 5mm (x50)
    // Línea de comentario
    # Otra nota
    """

    res = parse_bom_text(sample_text)
    print(f"  [OK] Ítems extraídos: {res.total_items} | Cantidad total acumulada: {res.total_quantity}")
    assert res.total_items == 10, f"Debe extraer 10 ítems válidos, obtuvo {res.total_items}"

    expected_samples = [
        (2, "ESP32 NodeMCU"),
        (10, "Resistencia 220 ohm 1/4W"),
        (1, "Sensor de temperatura DHT22"),
        (1, "Modulo Relay 5V 2 canales"),
        (1, "Pantalla OLED 0.96 I2C"),
        (5, "Arduino Uno R3"),
        (4, "LM358"),
        (100, "Resistencia 10k 1/4W"),
        (1, "Protoboard 830 puntos"),
        (50, "LED Rojo 5mm"),
    ]

    for item, (exp_qty, exp_name) in zip(res.items, expected_samples):
        assert item.quantity == exp_qty, f"Error en cantidad: esperado {exp_qty}, obtuvo {item.quantity}"
        assert item.product_query == exp_name, f"Error en nombre: esperado '{exp_name}', obtuvo '{item.product_query}'"
        print(f"   ✔ Cant: {item.quantity:>3} | Nombre: {item.product_query}")

    print("  [OK] Todos los formatos de BOM fueron interpretados con precisión.")

def test_match_scoring():
    print("\n--- 2. PROBANDO SCORING Y UMBRALES DE CONFIANZA ---")
    
    # Positive matches
    s1 = calculate_match_score("Sensor de temperatura DHT22", "MD-DHT22 Sensor de Temperatura y Humedad Digital", True)
    assert s1 >= 0.75, f"Score de DHT22 debe ser >= 0.75, obtuvo {s1}"
    print(f"  [OK] DHT22 vs MD-DHT22: Score {s1:.3f} (🟢 Alta Confianza)")

    # Negative / Different number match
    s2 = calculate_match_score("Sensor de temperatura DHT22", "MD-DHT11 Sensor de Temperatura y Humedad", True)
    assert s2 < 0.40, f"Score de DHT22 vs DHT11 debe ser < 0.40 por número diferente, obtuvo {s2}"
    print(f"  [OK] DHT22 vs DHT11: Score {s2:.3f} (🔴 Descartado por número incorrecto)")

    # Resistor values
    s3 = calculate_match_score("Resistencia 220 ohm 1/4W", "Resistencia 220 Ohm a 1/4 W", True)
    s4 = calculate_match_score("Resistencia 220 ohm 1/4W", "Resistencia 10k Ohm a 1/4 W", True)
    assert s3 > s4, "Resistencia 220 debe puntuar mucho más alto que 10k"
    print(f"  [OK] Resistencia 220 vs 220: {s3:.3f} | Resistencia 220 vs 10k: {s4:.3f}")

def test_parallel_bom_search():
    print("\n--- 3. PROBANDO BÚSQUEDA CONCURRENTE MULTILÍNEA ---")
    bom_input = """
    2x ESP32 NodeMCU
    10x Resistencia 220 ohm 1/4W
    Sensor de temperatura DHT22
    Modulo Relay 5V 2 canales
    Pantalla OLED 0.96 I2C
    """
    parse_res = parse_bom_text(bom_input)
    
    t0 = time.time()
    match_results = search_bom_items_parallel(parse_res.items, max_workers=5)
    elapsed = time.time() - t0
    
    print(f"  [OK] Búsqueda paralela de {len(match_results)} componentes completada en: {elapsed:.2f}s")
    assert len(match_results) == len(parse_res.items), "Debe devolver resultado para cada ítem"

    for idx, m in enumerate(match_results, 1):
        if m.best_match:
            print(f"   [{idx}] {m.bom_item.quantity:>2}x {m.bom_item.product_query:<28} ➔ {m.best_match.store_name:<16} | {m.best_match.title[:36]:<36} | Q {m.best_match.unit_price:>6.2f} | {m.status_badge}")
        else:
            print(f"   [{idx}] {m.bom_item.quantity:>2}x {m.bom_item.product_query:<28} ➔ {m.status_badge}")

    print("\n--- TODOS LOS TESTS DEL MOTOR BOM PASARON EXITOSAMENTE ---")

if __name__ == "__main__":
    test_bom_parser_formats()
    test_match_scoring()
    test_parallel_bom_search()
