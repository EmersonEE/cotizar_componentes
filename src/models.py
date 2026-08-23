from dataclasses import dataclass, field, asdict
from typing import List, Optional
from datetime import datetime

@dataclass
class Product:
    name: str
    url: str
    store_name: str
    unit_price: float
    currency: str = "GTQ"
    in_stock: bool = True
    stock_status: str = "Disponible"
    image_url: Optional[str] = None
    sku: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'Product':
        return cls(**data)

@dataclass
class QuoteItem:
    product: Product
    quantity: int
    unit_price: float
    subtotal: float

    def to_dict(self) -> dict:
        return {
            "product": self.product.to_dict(),
            "quantity": self.quantity,
            "unit_price": self.unit_price,
            "subtotal": self.subtotal
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'QuoteItem':
        product = Product.from_dict(data["product"])
        return cls(
            product=product,
            quantity=data["quantity"],
            unit_price=data["unit_price"],
            subtotal=data["subtotal"]
        )

@dataclass
class Customer:
    name: str = "Cliente General"
    phone: str = ""
    email: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'Customer':
        return cls(**data)

@dataclass
class BusinessInfo:
    name: str
    owner: str = ""
    phone: str = ""
    email: str = ""
    address: str = ""
    logo_url: str = ""
    payment_terms: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'BusinessInfo':
        return cls(**data)

@dataclass
class Quote:
    quote_id: str
    date: str
    valid_until: str
    customer: Customer
    items: List[QuoteItem] = field(default_factory=list)
    subtotal: float = 0.0
    service_fee_percent: float = 12.0
    service_fee_amount: float = 0.0
    total: float = 0.0
    currency_symbol: str = "Q"
    currency_code: str = "GTQ"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "quote_id": self.quote_id,
            "date": self.date,
            "valid_until": self.valid_until,
            "customer": self.customer.to_dict(),
            "items": [item.to_dict() for item in self.items],
            "subtotal": self.subtotal,
            "service_fee_percent": self.service_fee_percent,
            "service_fee_amount": self.service_fee_amount,
            "total": self.total,
            "currency_symbol": self.currency_symbol,
            "currency_code": self.currency_code,
            "created_at": self.created_at
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Quote':
        customer = Customer.from_dict(data["customer"])
        items = [QuoteItem.from_dict(it) for it in data["items"]]
        return cls(
            quote_id=data["quote_id"],
            date=data["date"],
            valid_until=data["valid_until"],
            customer=customer,
            items=items,
            subtotal=data["subtotal"],
            service_fee_percent=data["service_fee_percent"],
            service_fee_amount=data["service_fee_amount"],
            total=data["total"],
            currency_symbol=data.get("currency_symbol", "Q"),
            currency_code=data.get("currency_code", "GTQ"),
            created_at=data.get("created_at", "")
        )
