import json
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Any
from src.models import BusinessInfo
from src.stores import STORES

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


def _default_shipping_rules() -> Dict[str, Dict[str, Any]]:
    """Single source of truth for default shipping rules, derivada del registro
    central de tiendas (src/stores.py)."""
    return {
        store.name: {
            "free_threshold": store.free_threshold,
            "default_cost": store.default_shipping_cost,
            "is_pickup_only": store.is_pickup_only,
        }
        for store in STORES
    }


def _parse_bool(value: Any, default: bool = True) -> bool:
    """Lenient boolean parsing: accepts bool, int/float and common string forms
    ('true'/'1'/'yes'/'si'/'on', 'false'/'0'/'no'/'off')."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes", "si", "on"):
            return True
        if v in ("false", "0", "no", "off"):
            return False
    return default


@dataclass
class AppConfig:
    service_fee_percent: float = 10.0
    validity_days: int = 5
    currency_symbol: str = "Q"
    currency_code: str = "GTQ"
    quote_prefix: str = "COT"
    business: BusinessInfo = field(default_factory=lambda: BusinessInfo(name="Emerson Electrónica & Integración"))
    shipping_rules: Dict[str, Dict[str, Any]] = field(default_factory=_default_shipping_rules)

    enable_ai: bool = True
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    # Timeout (segundos) para la extracción de BOM con IA local. Mensajes largos
    # (BOMs de 30+ componentes) pueden tardar más de 15s en qwen2.5:7b.
    ai_timeout: float = 90.0

    @classmethod
    def load(cls, config_path: Path = DEFAULT_CONFIG_PATH) -> 'AppConfig':
        if not config_path.exists():
            default_cfg = cls()
            default_cfg.save(config_path)
            return default_cfg

        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        biz_data = data.get("business", {})
        business = BusinessInfo.from_dict(biz_data)

        shipping_rules = data.get("shipping_rules") or _default_shipping_rules()

        cfg = cls(
            service_fee_percent=float(data.get("service_fee_percent", 10.0)),
            validity_days=int(data.get("validity_days", 5)),
            currency_symbol=str(data.get("currency_symbol", "Q")),
            currency_code=str(data.get("currency_code", "GTQ")),
            quote_prefix=str(data.get("quote_prefix", "COT")),
            business=business,
            shipping_rules=shipping_rules,
            enable_ai=_parse_bool(data.get("enable_ai", True), True),
            ollama_url=str(data.get("ollama_url", "http://localhost:11434")),
            ollama_model=str(data.get("ollama_model", "qwen2.5:7b")),
            ai_timeout=float(data.get("ai_timeout", 90.0))
        )
        cfg._validate()
        return cfg

    def _validate(self):
        """Clamps invalid business values and logs warnings, keeping the app running."""
        if self.service_fee_percent < 0:
            logger.warning("service_fee_percent inválido (%.2f); se ajusta a 0.", self.service_fee_percent)
            self.service_fee_percent = 0.0
        if self.validity_days < 1:
            logger.warning("validity_days inválido (%d); se ajusta a 1.", self.validity_days)
            self.validity_days = 1
        if not self.quote_prefix or not str(self.quote_prefix).strip():
            logger.warning("quote_prefix vacío; se usa 'COT'.")
            self.quote_prefix = "COT"

        for store, rules in self.shipping_rules.items():
            threshold = rules.get("free_threshold")
            if threshold is not None and float(threshold) < 0:
                logger.warning("free_threshold negativo para '%s' (%.2f); se ajusta a 0.", store, threshold)
                rules["free_threshold"] = 0.0
            cost = rules.get("default_cost", 0.0)
            if float(cost) < 0:
                logger.warning("default_cost negativo para '%s' (%.2f); se ajusta a 0.", store, cost)
                rules["default_cost"] = 0.0

    def save(self, config_path: Path = DEFAULT_CONFIG_PATH):
        data = {
            "service_fee_percent": self.service_fee_percent,
            "validity_days": self.validity_days,
            "currency_symbol": self.currency_symbol,
            "currency_code": self.currency_code,
            "quote_prefix": self.quote_prefix,
            "business": self.business.to_dict() if self.business else {},
            "shipping_rules": self.shipping_rules,
            "enable_ai": self.enable_ai,
            "ollama_url": self.ollama_url,
            "ollama_model": self.ollama_model,
            "ai_timeout": self.ai_timeout
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
