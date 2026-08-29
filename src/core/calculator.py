from datetime import datetime, timedelta
from typing import List, Dict, Optional
from collections import defaultdict
from src.models import Product, QuoteItem, Quote, Customer, StoreShippingDetail

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
    def merge_duplicate_items(items: List[QuoteItem]) -> List[QuoteItem]:
        """
        Fusiona ítems repetidos (mismo SKU, o misma URL si no hay SKU, o mismo
        nombre si no hay ninguno), sumando cantidades y conservando el orden
        original. Usa el precio del primer ítem encontrado.
        Devuelve una nueva lista (no muta la entrada).
        """
        merged: Dict[str, QuoteItem] = {}
        order: List[str] = []

        for item in items:
            sku = (item.product.sku or "").strip().lower()
            url = (item.product.url or "").strip().lower()
            name = item.product.name.strip().lower()
            key = sku or url or name

            if key in merged:
                existing = merged[key]
                merged[key] = QuoteCalculator.create_quote_item(
                    existing.product, existing.quantity + item.quantity
                )
            else:
                merged[key] = item
                order.append(key)

        return [merged[k] for k in order]

    @staticmethod
    def group_items_by_store(items: List[QuoteItem]) -> Dict[str, List[QuoteItem]]:
        """Groups a list of quote items by their origin store name."""
        grouped = defaultdict(list)
        for item in items:
            grouped[item.product.store_name].append(item)
        return dict(grouped)

    @staticmethod
    def calculate_store_subtotals(items: List[QuoteItem]) -> Dict[str, float]:
        """Calculates total purchase amount for each store."""
        subtotals = defaultdict(float)
        for item in items:
            subtotals[item.product.store_name] += item.subtotal
        return {store: round(sub, 2) for store, sub in subtotals.items()}

    @staticmethod
    def evaluate_shipping_details(
        store_subtotals: Dict[str, float],
        shipping_rules: Dict[str, dict],
        custom_shipping_costs: Optional[Dict[str, float]] = None
    ) -> List[StoreShippingDetail]:
        """
        Evaluates shipping rules per store based on store subtotals.
        Returns a list of StoreShippingDetail objects.
        """
        custom_shipping_costs = custom_shipping_costs or {}
        details: List[StoreShippingDetail] = []

        for store_name, subtotal in store_subtotals.items():
            rule = shipping_rules.get(store_name, {})
            is_pickup = rule.get("is_pickup_only", False)
            threshold = rule.get("free_threshold")
            default_cost = float(rule.get("default_cost", 35.0))

            if store_name in custom_shipping_costs:
                cost = round(float(custom_shipping_costs[store_name]), 2)
                details.append(StoreShippingDetail(
                    store_name=store_name,
                    items_subtotal=subtotal,
                    free_threshold=threshold,
                    qualifies_free=(cost == 0.0),
                    shipping_cost=cost,
                    status_label="No aplica (Retiro en tienda)" if cost == 0.0 else f"Q {cost:,.2f}",
                    is_pickup_only=(is_pickup and cost == 0.0),
                    shipping_was_custom=True
                ))
            elif is_pickup or threshold is None:
                # Store doesn't do shipping / in-store pickup
                details.append(StoreShippingDetail(
                    store_name=store_name,
                    items_subtotal=subtotal,
                    free_threshold=None,
                    qualifies_free=True,
                    shipping_cost=0.0,
                    status_label="No aplica (Retiro en tienda)",
                    is_pickup_only=True,
                    shipping_was_custom=False
                ))
            elif subtotal >= threshold:
                # Free shipping minimum reached
                details.append(StoreShippingDetail(
                    store_name=store_name,
                    items_subtotal=subtotal,
                    free_threshold=threshold,
                    qualifies_free=True,
                    shipping_cost=0.0,
                    status_label=f"Gratis (mínimo Q{threshold:,.0f} alcanzado)",
                    is_pickup_only=False,
                    shipping_was_custom=False
                ))
            else:
                # Free shipping minimum NOT reached; apply shipping cost
                cost = round(float(default_cost), 2)
                details.append(StoreShippingDetail(
                    store_name=store_name,
                    items_subtotal=subtotal,
                    free_threshold=threshold,
                    qualifies_free=False,
                    shipping_cost=cost,
                    status_label=f"Q {cost:,.2f}",
                    is_pickup_only=False,
                    shipping_was_custom=False
                ))

        return details

    @staticmethod
    def build_quote(
        quote_id: str,
        items: List[QuoteItem],
        customer: Customer,
        shipping_details: Optional[List[StoreShippingDetail]] = None,
        shipping_rules: Optional[Dict[str, dict]] = None,
        custom_shipping_costs: Optional[Dict[str, float]] = None,
        discount_percent: float = 0.0,
        discount_amount: float = 0.0,
        service_fee_percent: float = 10.0,
        validity_days: int = 5,
        version: int = 1,
        base_quote_id: Optional[str] = None,
        currency_symbol: str = "Q",
        currency_code: str = "GTQ"
    ) -> Quote:
        if not items:
            raise ValueError("La cotización debe tener al menos un ítem.")

        # 1. Components Subtotal
        items_subtotal = round(sum(item.subtotal for item in items), 2)

        # 2. Discount calculation
        if discount_percent > 0:
            calc_discount = round(items_subtotal * (discount_percent / 100.0), 2)
        else:
            calc_discount = round(discount_amount, 2)
        calc_discount = max(0.0, min(calc_discount, items_subtotal))
        subtotal_after_discount = round(items_subtotal - calc_discount, 2)

        # 3. Service fee (calculated over subtotal after discount)
        service_fee_amount = round(subtotal_after_discount * (service_fee_percent / 100.0), 2)

        # 4. Shipping details
        if shipping_details is None:
            if shipping_rules is None:
                shipping_rules = {}
            store_subtotals = QuoteCalculator.calculate_store_subtotals(items)
            shipping_details = QuoteCalculator.evaluate_shipping_details(
                store_subtotals,
                shipping_rules,
                custom_shipping_costs
            )

        total_shipping = round(sum(sd.shipping_cost for sd in shipping_details), 2)

        # 5. Grand Total = Subtotal after discount + Service Fee + Shipping
        total = round(subtotal_after_discount + service_fee_amount + total_shipping, 2)

        now = datetime.now()
        date_str = now.strftime("%d/%m/%Y")
        valid_until_date = now + timedelta(days=validity_days)
        valid_until_str = valid_until_date.strftime("%d/%m/%Y")

        return Quote(
            quote_id=quote_id,
            version=version,
            base_quote_id=base_quote_id,
            date=date_str,
            valid_until=valid_until_str,
            customer=customer,
            items=items,
            shipping_details=shipping_details,
            items_subtotal=items_subtotal,
            discount_percent=discount_percent,
            discount_amount=calc_discount,
            service_fee_percent=service_fee_percent,
            service_fee_amount=service_fee_amount,
            total_shipping=total_shipping,
            total=total,
            currency_symbol=currency_symbol,
            currency_code=currency_code,
            validity_days=validity_days,
            created_at=now.isoformat(),
            updated_at=now.isoformat()
        )
