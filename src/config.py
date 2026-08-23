import json
import os
from pathlib import Path
from dataclasses import dataclass
from src.models import BusinessInfo

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

@dataclass
class AppConfig:
    service_fee_percent: float = 12.0
    validity_days: int = 5
    currency_symbol: str = "Q"
    currency_code: str = "GTQ"
    quote_prefix: str = "COT"
    business: BusinessInfo = None

    @classmethod
    def load(cls, config_path: Path = DEFAULT_CONFIG_PATH) -> 'AppConfig':
        if not config_path.exists():
            default_cfg = cls(business=BusinessInfo(name="Emerson Electrónica & Integración"))
            default_cfg.save(config_path)
            return default_cfg

        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        biz_data = data.get("business", {})
        business = BusinessInfo.from_dict(biz_data)

        return cls(
            service_fee_percent=float(data.get("service_fee_percent", 12.0)),
            validity_days=int(data.get("validity_days", 5)),
            currency_symbol=data.get("currency_symbol", "Q"),
            currency_code=data.get("currency_code", "GTQ"),
            quote_prefix=data.get("quote_prefix", "COT"),
            business=business
        )

    def save(self, config_path: Path = DEFAULT_CONFIG_PATH):
        data = {
            "service_fee_percent": self.service_fee_percent,
            "validity_days": self.validity_days,
            "currency_symbol": self.currency_symbol,
            "currency_code": self.currency_code,
            "quote_prefix": self.quote_prefix,
            "business": self.business.to_dict() if self.business else {}
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
