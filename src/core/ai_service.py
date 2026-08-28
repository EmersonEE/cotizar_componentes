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
3. Limpia el nombre del componente para que sea un término de búsqueda óptimo y conciso en tiendas de electrónica (ej. 'Arduino Uno R3', 'Resistencia 220 ohm 1/4W', 'Pantalla OLED 0.96 I2C', 'Sensor ultrasónico HC-SR04', 'Módulo Bluetooth HC-05', 'Servomotor SG90', 'Fuente 12V 2A', 'Cable Dupont macho-hembra').
4. Ignora saludos, despedidas, preguntas de precios, charlas o comentarios irrelevantes.
5. Responde ÚNICAMENTE con un objeto JSON válido con la siguiente estructura:
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
