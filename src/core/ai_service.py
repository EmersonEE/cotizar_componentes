import json
import logging
from typing import List, Dict, Optional, Any
import httpx

logger = logging.getLogger(__name__)

def check_ollama_status(host: str = "http://localhost:11434", timeout: float = 1.5) -> bool:
    """Checks if Ollama service is reachable locally."""
    try:
        url = f"{host.rstrip('/')}/api/tags"
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url)
            return resp.status_code == 200
    except Exception:
        return False

def extract_bom_with_ai(
    raw_text: str,
    host: str = "http://localhost:11434",
    model: str = "qwen2.5:7b",
    timeout: float = 15.0
) -> Optional[List[Dict[str, Any]]]:
    """
    Extracts electronics components and quantities from unformatted or conversational
    text (e.g. WhatsApp messages, notes) using local Ollama LLM.
    Returns a list of dicts: [{"cantidad": int, "componente": str}] or None if failed.
    """
    if not raw_text or not raw_text.strip():
        return None

    prompt = f"""Eres un ingeniero de hardware y experto en compras de componentes electrónicos en Guatemala.
Tu tarea es leer el siguiente mensaje o lista libre y extraer todos los componentes electrónicos solicitados con su cantidad correspondiente.

Reglas estrictas:
1. Normaliza la cantidad a un número entero (mínimo 1). Si no se especifica cantidad, asume 1.
2. Si el texto desglosa una cantidad total en opciones o variantes (ej. '2 Arduinos, uno con cable y otro sin cable' o '20 LEDs, 10 rojos y 10 azules'), extrae ÚNICAMENTE los ítems específicos desglosados (ej. 1x Arduino Uno con cable, 1x Arduino Uno sin cable; 10x LED rojo, 10x LED azul) y NUNCA agregues un ítem duplicado con el total general.
3. Limpia el nombre del componente para que sea un término de búsqueda óptimo y conciso en tiendas de electrónica (ej. 'Arduino Uno R3', 'Resistencia 220 ohm 1/4W', 'Pantalla OLED 0.96 I2C', 'Sensor ultrasónico HC-SR04', 'Módulo Bluetooth HC-05', 'Servomotor SG90', 'Fuente 12V 2A', 'Cable Dupont macho-hembra', 'Caja organizadora', 'Cautín 60W').
4. Ignora saludos, despedidas, preguntas de precios, charlas o comentarios irrelevantes.
5. NO omitas componentes pedidos de forma condicional o complementaria: si el texto dice 'si tienen disponible, agréguenme también X', 'también necesito Y', 'adicionalmente Z' o similar, extráelos igualmente con su cantidad (o 1 si no se especifica).
6. Responde ÚNICAMENTE con un objeto JSON válido con la siguiente estructura:
{{
  "items": [
    {{
      "cantidad": 2,
      "componente": "Pantalla OLED 0.96 I2C"
    }},
    {{
      "cantidad": 10,
      "componente": "Resistencia 220 ohm 1/4W"
    }}
  ]
}}

Texto a analizar:
\"\"\"{raw_text.strip()}\"\"\"
"""

    payload = {
        "model": model,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0.1,
            "top_p": 0.9
        }
    }

    try:
        url = f"{host.rstrip('/')}/api/generate"
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload)
            if resp.status_code != 200:
                logger.warning(f"Ollama returned status code {resp.status_code}")
                return None

            data = resp.json()
            raw_response = data.get("response", "")
            if not raw_response:
                return None

            parsed = json.loads(raw_response)
            items_raw = parsed.get("items", [])
            
            # Format and validate extracted items
            validated_items: List[Dict[str, Any]] = []
            for it in items_raw:
                comp = str(it.get("componente", "")).strip()
                if comp:
                    try:
                        qty = max(1, int(it.get("cantidad", 1)))
                    except (ValueError, TypeError):
                        qty = 1
                    validated_items.append({
                        "cantidad": qty,
                        "componente": comp
                    })

            return validated_items if validated_items else None

    except Exception as e:
        logger.warning(f"Failed to extract BOM with AI: {e}")
        return None

def verify_matches_with_ai(
    items: List[Dict[str, Any]],
    host: str = "http://localhost:11434",
    model: str = "qwen2.5:7b",
    timeout: float = 90.0,
) -> Optional[List[Dict[str, Any]]]:
    """
    Verifica EN LOTE si cada producto candidato corresponde realmente al componente
    solicitado en el BOM (y si su precio parece razonable). Una sola llamada a Ollama.

    items: [{"componente": "LED rojo 5 mm", "candidato": "FE-305D Fuente 30Vcc...",
             "tienda": "Electrónica RyCH", "precio": 630.0}, ...]

    Devuelve [{"componente": str, "match": bool, "precio_ok": bool, "razon": str}]
    alineado con los componentes de entrada, o None si la IA falla.
    """
    if not items:
        return None

    lista_json = json.dumps(items, ensure_ascii=False)

    prompt = f"""Eres un ingeniero electrónico senior experto en componentes y precios del mercado de Guatemala.
Tu tarea es verificar, para CADA elemento de la lista, si el "candidato" (producto encontrado en una tienda)
corresponde realmente al "componente" solicitado por el cliente.

Criterios:
1. "match": true SOLO si el candidato ES el componente solicitado o un equivalente directo funcional
   (misma función y especificaciones clave). Si es un componente distinto (ej. pidieron 'LED rojo 5mm' y el
   candidato es una 'Fuente de alimentación', o pidieron 'Resistencia 220 ohm' y el candidato es una
   'Resistencia 10k ohm'), responde match: false.
2. "precio_ok": false si el precio parece anormalmente alto o bajo para ese componente (suele indicar un
   emparejamiento incorrecto o un producto distinto).
3. Sé estricto: ante la duda de que sea otro componente, responde match: false con una razón corta.

Lista a verificar:
{lista_json}

Responde ÚNICAMENTE con un objeto JSON válido con esta estructura (UN elemento por cada componente de la lista, en el mismo orden):
{{
  "verificaciones": [
    {{"componente": "LED rojo 5 mm", "match": false, "precio_ok": false, "razon": "El candidato es una fuente de poder, no un LED"}}
  ]
}}
"""

    payload = {
        "model": model,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0.1,
            "top_p": 0.9
        }
    }

    try:
        url = f"{host.rstrip('/')}/api/generate"
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload)
            if resp.status_code != 200:
                logger.warning("verify_matches_with_ai: Ollama respondió status %s", resp.status_code)
                return None

            data = resp.json()
            raw_response = data.get("response", "")
            if not raw_response:
                return None

            parsed = json.loads(raw_response)
            verifications_raw = parsed.get("verificaciones", [])
            if not isinstance(verifications_raw, list):
                return None

            verified: List[Dict[str, Any]] = []
            for v in verifications_raw:
                if not isinstance(v, dict):
                    continue
                comp = str(v.get("componente", "")).strip()
                if not comp:
                    continue
                verified.append({
                    "componente": comp,
                    "match": bool(v.get("match", False)),
                    "precio_ok": bool(v.get("precio_ok", True)),
                    "razon": str(v.get("razon", "")).strip(),
                })

            return verified if verified else None

    except Exception as e:
        logger.warning("Failed to verify matches with AI: %s", e)
        return None


def suggest_alternatives_with_ai(
    component_name: str,
    reason: str = "Agotado o no encontrado en tiendas locales",
    host: str = "http://localhost:11434",
    model: str = "qwen2.5:7b",
    timeout: float = 15.0
) -> List[Dict[str, str]]:
    """
    Suggests 2 to 4 pin-compatible or functional electronic component replacements/alternatives
    using local Ollama LLM.
    Returns a list of dicts: [{"nombre": str, "explicacion": str, "compatibilidad": str}]
    """
    if not component_name or not component_name.strip():
        return []

    prompt = f"""Eres un ingeniero electrónico senior experto en diseño de circuitos y sustitución de componentes.
Un cliente necesita el siguiente componente electrónico: "{component_name.strip()}", pero actualmente está {reason}.

Sugiere entre 2 y 4 componentes alternativos, equivalentes directos o reemplazos funcionales que sean comunes y fáciles de conseguir en tiendas de electrónica de Guatemala.

Responde ÚNICAMENTE con un objeto JSON válido con la siguiente estructura:
{{
  "alternativas": [
    {{
      "nombre": "Nombre exacto del componente alternativo (ej. ESP32 DevKit V1)",
      "explicacion": "Breve explicación de por qué sirve como reemplazo (ej. Mayor potencia y compatible con WiFi/Bluetooth)",
      "compatibilidad": "Nivel de compatibilidad (ej. 'Directa (Pin a Pin)', 'Funcional (Requiere adaptar código/pines)', o 'Superior')"
    }}
  ]
}}
"""

    payload = {
        "model": model,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0.2,
            "top_p": 0.9
        }
    }

    try:
        url = f"{host.rstrip('/')}/api/generate"
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload)
            if resp.status_code != 200:
                return []

            data = resp.json()
            raw_response = data.get("response", "")
            if not raw_response:
                return []

            parsed = json.loads(raw_response)
            alternatives_raw = parsed.get("alternativas", [])
            
            validated: List[Dict[str, str]] = []
            for alt in alternatives_raw:
                nombre = str(alt.get("nombre", "")).strip()
                if nombre:
                    validated.append({
                        "nombre": nombre,
                        "explicacion": str(alt.get("explicacion", "Alternativa funcional compatible")).strip(),
                        "compatibilidad": str(alt.get("compatibilidad", "Funcional")).strip()
                    })

            return validated

    except Exception as e:
        logger.warning(f"Failed to suggest alternatives with AI: {e}")
        return []
