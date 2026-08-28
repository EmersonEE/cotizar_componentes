import re
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict, Any

from src.config import AppConfig
from src.core.ai_service import extract_bom_with_ai, check_ollama_status

@dataclass
class ParsedBOMItem:
    """Represents an extracted component from a BOM line."""
    raw_line: str
    quantity: int
    product_query: str
    is_valid: bool = True
    error_msg: Optional[str] = None

    def __repr__(self) -> str:
        return f"<ParsedBOMItem {self.quantity}x '{self.product_query}'>"

@dataclass
class BOMParseResult:
    """Consolidated result of parsing a multiline BOM block."""
    items: List[ParsedBOMItem]
    invalid_lines: List[str]
    source: str = "regex"  # "regex" or "ai_ollama"

    @property
    def total_items(self) -> int:
        return len(self.items)

    @property
    def total_quantity(self) -> int:
        return sum(item.quantity for item in self.items if item.is_valid)

def parse_bom_line(line: str) -> Optional[ParsedBOMItem]:
    """
    Parses a single line of text into a quantity and product query.
    Tolerates diverse formats while strictly protecting electronic model numbers
    (e.g., 'DHT22', 'LM358', 'ESP32', 'NE555', '0.96') from being misread as quantities.
    """
    raw = line.strip()
    if not raw:
        return None

    # Ignore comment lines starting with # or //
    if raw.startswith(("#", "//")):
        return None

    # 1. Clean leading bullets and numbering (e.g. "- ", "* ", "• ", "1. ", "2) ", "> ")
    cleaned = re.sub(r"^(?:[\d]+[\.\)\-]\s*|[\-\*\•\>]\s*)", "", raw).strip()
    if not cleaned:
        return None

    # 2. Leading quantity patterns:
    # "5x ESP32", "5 x ESP32", "5X ESP32", "5× ESP32", "5* ESP32", "5 - ESP32", "5 pcs ESP32", "5 uds ESP32"
    m_lead = re.match(
        r"^(\d+)\s*(?:[xX×\*]|\b(?:unidades|unidad|uds|pcs|piezas|cant|cantidad)\b|\-)?\s*(.+)$",
        cleaned
    )
    if m_lead:
        qty_str, item_name = m_lead.group(1), m_lead.group(2).strip()
        item_name = re.sub(r"[,;]+$", "", item_name).strip()
        if item_name:
            qty = max(1, int(qty_str))
            return ParsedBOMItem(raw_line=raw, quantity=qty, product_query=item_name, is_valid=True)

    # 3. Trailing quantity patterns with EXPLICIT markers:
    # "ESP32 (x5)", "ESP32 (5x)", "ESP32 [5 uds]", "ESP32 - 5x", "ESP32 x 5"
    m_trail = re.search(
        r"[\s,\(\[\-]+(?:[xX×]\s*(\d+)|\b(?:cant|cantidad|unidades|uds|pcs)\s*[:=]?\s*(\d+)|(\d+)\s*[xX×]|\b(\d+)\s*(?:unidades|uds|pcs|piezas)\b)[\)\]]?$",
        cleaned,
        re.IGNORECASE
    )
    if m_trail:
        qty_val = next(g for g in m_trail.groups() if g is not None)
        item_name = cleaned[:m_trail.start()].strip()
        item_name = re.sub(r"[,;]+$", "", item_name).strip()
        if item_name:
            qty = max(1, int(qty_val))
            return ParsedBOMItem(raw_line=raw, quantity=qty, product_query=item_name, is_valid=True)

    # 4. Fallback: Entire cleaned line is the product name with default quantity 1
    fallback_name = re.sub(r"[,;]+$", "", cleaned).strip()
    if fallback_name:
        return ParsedBOMItem(raw_line=raw, quantity=1, product_query=fallback_name, is_valid=True)

    return ParsedBOMItem(raw_line=raw, quantity=1, product_query=raw, is_valid=False, error_msg="Línea no interpretable")

def parse_bom_text(text: str) -> BOMParseResult:
    """
    Parses a multiline BOM block into structured items using classic fast regex rules.
    Handles empty lines and reports invalid lines without failing the entire batch.
    """
    items: List[ParsedBOMItem] = []
    invalid_lines: List[str] = []

    for line in text.strip().splitlines():
        parsed = parse_bom_line(line)
        if parsed is None:
            continue
        if parsed.is_valid:
            items.append(parsed)
        else:
            invalid_lines.append(parsed.raw_line)

    return BOMParseResult(items=items, invalid_lines=invalid_lines, source="regex")

def parse_bom_text_hybrid(
    text: str,
    config: Optional[AppConfig] = None,
    force_ai: bool = False
) -> BOMParseResult:
    """
    Hybrid BOM parser:
    - If force_ai is True or if text is conversational/unstructured and AI is enabled,
      uses Ollama LLM to extract items with structured JSON output.
    - If AI fails or is disabled, gracefully falls back to classic regex parser.
    """
    if not text or not text.strip():
        return BOMParseResult(items=[], invalid_lines=[], source="regex")

    cfg = config or AppConfig.load()

    # If AI is enabled or forced, attempt extraction with Ollama
    if force_ai or cfg.enable_ai:
        ai_items = extract_bom_with_ai(
            raw_text=text,
            host=cfg.ollama_url,
            model=cfg.ollama_model,
            timeout=15.0
        )
        if ai_items:
            parsed_items: List[ParsedBOMItem] = []
            for it in ai_items:
                parsed_items.append(ParsedBOMItem(
                    raw_line=f"{it['cantidad']}x {it['componente']}",
                    quantity=it['cantidad'],
                    product_query=it['componente'],
                    is_valid=True
                ))
            return BOMParseResult(items=parsed_items, invalid_lines=[], source="ai_ollama")

    # Fallback to standard regex parser
    return parse_bom_text(text)
