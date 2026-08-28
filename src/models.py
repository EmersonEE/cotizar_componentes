import re
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
        if not isinstance(data, dict):
            return cls(name="Desconocido", url="", store_name="N/A", unit_price=0.0)
        return cls(
            name=str(data.get("name", "Desconocido")),
            url=str(data.get("url", "")),
            store_name=str(data.get("store_name", "N/A")),
            unit_price=float(data.get("unit_price", 0.0)),
            currency=str(data.get("currency", "GTQ")),
            in_stock=bool(data.get("in_stock", True)),
            stock_status=str(data.get("stock_status", "Disponible")),
            image_url=data.get("image_url"),
            sku=data.get("sku")
        )

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
        if not isinstance(data, dict):
            return cls(product=Product("Desconocido", "", "N/A", 0.0), quantity=1, unit_price=0.0, subtotal=0.0)
        product = Product.from_dict(data.get("product", {}))
        qty = max(1, int(data.get("quantity", 1)))
        unit_price = float(data.get("unit_price", product.unit_price))
        subtotal = float(data.get("subtotal", round(qty * unit_price, 2)))
        return cls(
            product=product,
            quantity=qty,
            unit_price=unit_price,
            subtotal=subtotal
        )

@dataclass
class Customer:
    name: str = "Cliente General"
    phone: str = ""
    email: str = ""
    notes: str = ""

    def validate(self) -> List[str]:
        """Validates customer data and returns a list of error/warning messages if any."""
        errors = []
        if self.email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", self.email.strip()):
            errors.append(f"El correo '{self.email}' no tiene un formato válido.")
        return errors

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'Customer':
        if not isinstance(data, dict):
            return cls()
        return cls(
            name=str(data.get("name", "Cliente General")).strip() or "Cliente General",
            phone=str(data.get("phone", "")).strip(),
            email=str(data.get("email", "")).strip(),
            notes=str(data.get("notes", "")).strip()
        )

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
        if not isinstance(data, dict):
            return cls(name="Empresa")
        return cls(
            name=str(data.get("name", "Empresa")),
            owner=str(data.get("owner", "")),
            phone=str(data.get("phone", "")),
            email=str(data.get("email", "")),
            address=str(data.get("address", "")),
            logo_url=str(data.get("logo_url", "")),
            payment_terms=str(data.get("payment_terms", ""))
        )

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
        if not isinstance(data, dict):
            return cls(store_name="N/A", items_subtotal=0.0)
        return cls(
            store_name=str(data.get("store_name", "N/A")),
            items_subtotal=float(data.get("items_subtotal", 0.0)),
            free_threshold=float(data["free_threshold"]) if data.get("free_threshold") is not None else None,
            qualifies_free=bool(data.get("qualifies_free", False)),
            shipping_cost=float(data.get("shipping_cost", 0.0)),
            status_label=str(data.get("status_label", "")),
            is_pickup_only=bool(data.get("is_pickup_only", False))
        )

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
            "subtotal": self.items_subtotal,
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
        customer = Customer.from_dict(data.get("customer", {}))
        items = [QuoteItem.from_dict(it) for it in data.get("items", [])]
        shipping_details = [
            StoreShippingDetail.from_dict(sd)
            for sd in data.get("shipping_details", [])
        ]
        
        items_subtotal = float(data.get("items_subtotal", data.get("subtotal", 0.0)))
        total_shipping = float(data.get("total_shipping", 0.0))

        return cls(
            quote_id=str(data.get("quote_id", "")),
            version=int(data.get("version", 1)),
            base_quote_id=data.get("base_quote_id"),
            date=str(data.get("date", "")),
            valid_until=str(data.get("valid_until", "")),
            customer=customer,
            items=items,
            shipping_details=shipping_details,
            items_subtotal=items_subtotal,
            service_fee_percent=float(data.get("service_fee_percent", 12.0)),
            service_fee_amount=float(data.get("service_fee_amount", 0.0)),
            total_shipping=total_shipping,
            total=float(data.get("total", 0.0)),
            currency_symbol=str(data.get("currency_symbol", "Q")),
            currency_code=str(data.get("currency_code", "GTQ")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", ""))
        )
