from datetime import datetime, timedelta
from typing import List
from src.models import Product, QuoteItem, Quote, Customer

def format_currency(amount: float, symbol: str = "Q") -> str:
    """Formats a float as Guatemalan Quetzal currency: 'Q 1,250.00'."""
    return f"{symbol} {amount:,.2f}"

class QuoteCalculator:
    @staticmethod
    def create_quote_item(product: Product, quantity: int) -> QuoteItem:
        if quantity <= 0:
            raise ValueError("La cantidad debe ser mayor a 0")
        subtotal = round(product.unit_price * quantity, 2)
        return QuoteItem(
            product=product,
            quantity=quantity,
            unit_price=product.unit_price,
            subtotal=subtotal
        )

    @staticmethod
    def build_quote(
        quote_id: str,
        items: List[QuoteItem],
        customer: Customer,
        service_fee_percent: float = 12.0,
        validity_days: int = 5,
        currency_symbol: str = "Q",
        currency_code: str = "GTQ"
    ) -> Quote:
        if not items:
            raise ValueError("La cotización debe tener al menos un ítem.")

        subtotal = sum(item.subtotal for item in items)
        subtotal = round(subtotal, 2)

        service_fee_amount = round(subtotal * (service_fee_percent / 100.0), 2)
        total = round(subtotal + service_fee_amount, 2)

        now = datetime.now()
        date_str = now.strftime("%d/%m/%Y")
        valid_until_date = now + timedelta(days=validity_days)
        valid_until_str = valid_until_date.strftime("%d/%m/%Y")

        return Quote(
            quote_id=quote_id,
            date=date_str,
            valid_until=valid_until_str,
            customer=customer,
            items=items,
            subtotal=subtotal,
            service_fee_percent=service_fee_percent,
            service_fee_amount=service_fee_amount,
            total=total,
            currency_symbol=currency_symbol,
            currency_code=currency_code,
            created_at=now.isoformat()
        )
