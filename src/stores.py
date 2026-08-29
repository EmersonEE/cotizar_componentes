"""Registro central de tiendas soportadas (T10).

Fuente única de verdad para nombres, dominios y reglas de envío por tienda.
Evita strings hardcodeados dispersos en scrapers, config, UIs y optimizador.
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class Store:
    name: str
    domain: str
    domain_aliases: Tuple[str, ...] = ()
    is_pickup_only: bool = False
    free_threshold: Optional[float] = None
    default_shipping_cost: float = 0.0


STORES: List[Store] = [
    Store(
        name="La Electrónica",
        domain="laelectronica.com.gt",
        domain_aliases=("www.laelectronica.com.gt",),
        is_pickup_only=False,
        free_threshold=150.0,
        default_shipping_cost=35.0,
    ),
    Store(
        name="Electrónica DIY",
        domain="electronicadiy.com",
        domain_aliases=("www.electronicadiy.com",),
        is_pickup_only=False,
        free_threshold=250.0,
        default_shipping_cost=35.0,
    ),
    Store(
        name="Electrónica RyCH",
        domain="electronicarych.com",
        domain_aliases=("www.electronicarych.com",),
        is_pickup_only=True,
        free_threshold=None,
        default_shipping_cost=0.0,
    ),
]

STORE_NAMES: List[str] = [s.name for s in STORES]
STORE_DOMAINS: List[str] = [s.domain for s in STORES]
