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
class StoreShippingDetail:
    store_name: str
    items_subtotal: float
    free_threshold: Optional[float] = None
    qualifies_free: bool = False
    shipping_cost: float = 0.0
    status_label: str = ""
    is_pickup_only: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'StoreShippingDetail':
        return cls(**data)

@dataclass
class Quote:
    quote_id: str
    date: str
    valid_until: str
    customer: Customer
    items: List[QuoteItem] = field(default_factory=list)
    shipping_details: List[StoreShippingDetail] = field(default_factory=list)
    items_subtotal: float = 0.0
    service_fee_percent: float = 12.0
    service_fee_amount: float = 0.0
    total_shipping: float = 0.0
    total: float = 0.0
    version: int = 1
    base_quote_id: Optional[str] = None
    currency_symbol: str = "Q"
    currency_code: str = "GTQ"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    # Backwards compatibility property
    @property
    def subtotal(self) -> float:
        return self.items_subtotal

    def to_dict(self) -> dict:
        return {
            "quote_id": self.quote_id,
            "version": self.version,
            "base_quote_id": self.base_quote_id,
            "date": self.date,
            "valid_until": self.valid_until,
            "customer": self.customer.to_dict(),
            "items": [item.to_dict() for item in self.items],
            "shipping_details": [sd.to_dict() for sd in self.shipping_details],
            "items_subtotal": self.items_subtotal,
            "subtotal": self.items_subtotal,  # for backwards compatibility
            "service_fee_percent": self.service_fee_percent,
            "service_fee_amount": self.service_fee_amount,
            "total_shipping": self.total_shipping,
            "total": self.total,
            "currency_symbol": self.currency_symbol,
            "currency_code": self.currency_code,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Quote':
        customer = Customer.from_dict(data["customer"])
        items = [QuoteItem.from_dict(it) for it in data.get("items", [])]
        shipping_details = [
            StoreShippingDetail.from_dict(sd)
            for sd in data.get("shipping_details", [])
        ]
        
        items_subtotal = data.get("items_subtotal", data.get("subtotal", 0.0))
        total_shipping = data.get("total_shipping", 0.0)

        return cls(
            quote_id=data["quote_id"],
            version=data.get("version", 1),
            base_quote_id=data.get("base_quote_id"),
            date=data["date"],
            valid_until=data["valid_until"],
            customer=customer,
            items=items,
            shipping_details=shipping_details,
            items_subtotal=items_subtotal,
            service_fee_percent=data.get("service_fee_percent", 12.0),
            service_fee_amount=data.get("service_fee_amount", 0.0),
            total_shipping=total_shipping,
            total=data.get("total", 0.0),
            currency_symbol=data.get("currency_symbol", "Q"),
            currency_code=data.get("currency_code", "GTQ"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", "")
        )
