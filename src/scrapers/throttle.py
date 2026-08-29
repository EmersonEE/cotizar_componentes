"""Throttling global para requests HTTP a las tiendas.

Limita la concurrencia real de requests a través de todos los scrapers y
hilos de búsqueda (metabuscador, BOM paralelo, re-verificación) para reducir
el riesgo de bloqueos 429/rate-limit por parte de las tiendas.
"""
import threading
from contextlib import contextmanager
from typing import Iterator

MAX_CONCURRENT_HTTP_REQUESTS = 8

_GLOBAL_HTTP_SEMAPHORE = threading.BoundedSemaphore(MAX_CONCURRENT_HTTP_REQUESTS)


@contextmanager
def throttled_http_request() -> Iterator[None]:
    """Contexto que limita el número de requests HTTP simultáneos."""
    with _GLOBAL_HTTP_SEMAPHORE:
        yield
