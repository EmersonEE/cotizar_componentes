import json
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
from src.models import BusinessInfo

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

DEFAULT_SHIPPING_RULES = {
    "La Electrónica": {
        "free_threshold": 150.0,
        "default_cost": 35.0,
        "is_pickup_only": False
    },
    "Electrónica DIY": {
        "free_threshold": 250.0,
        "default_cost": 35.0,
        "is_pickup_only": False
    },
    "Electrónica RyCH": {
        "free_threshold": None,
        "default_cost": 0.0,
        "is_pickup_only": True
    }
}

@dataclass
class AppConfig:
    service_fee_percent: float = 12.0
    validity_days: int = 5
    currency_symbol: str = "Q"
    currency_code: str = "GTQ"
    quote_prefix: str = "COT"
    business: BusinessInfo = field(default_factory=lambda: BusinessInfo(name="Emerson Electrónica & Integración"))
    shipping_rules: Dict[str, Dict[str, Any]] = field(default_factory=lambda: {
        "La Electrónica": {"free_threshold": 150.0, "default_cost": 35.0, "is_pickup_only": False},
        "Electrónica DIY": {"free_threshold": 250.0, "default_cost": 35.0, "is_pickup_only": False},
        "Electrónica RyCH": {"free_threshold": None, "default_cost": 0.0, "is_pickup_only": True},
    })

    enable_ai: bool = True
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"

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

        shipping_rules = data.get("shipping_rules", {
            "La Electrónica": {"free_threshold": 150.0, "default_cost": 35.0, "is_pickup_only": False},
            "Electrónica DIY": {"free_threshold": 250.0, "default_cost": 35.0, "is_pickup_only": False},
            "Electrónica RyCH": {"free_threshold": None, "default_cost": 0.0, "is_pickup_only": True},
        })

        return cls(
            service_fee_percent=float(data.get("service_fee_percent", 12.0)),
            validity_days=int(data.get("validity_days", 5)),
            currency_symbol=data.get("currency_symbol", "Q"),
            currency_code=data.get("currency_code", "GTQ"),
            quote_prefix=data.get("quote_prefix", "COT"),
            business=business,
            shipping_rules=shipping_rules,
            enable_ai=bool(data.get("enable_ai", True)),
            ollama_url=str(data.get("ollama_url", "http://localhost:11434")),
            ollama_model=str(data.get("ollama_model", "qwen2.5:7b"))
        )

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
            "ollama_model": self.ollama_model
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
