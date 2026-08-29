"""Throttling global y por dominio para requests HTTP a las tiendas.

Limita la concurrencia real y añade pausas inteligentes entre requests hacia el
mismo dominio (especialmente tiendas Shopify) para evitar bloqueos HTTP 429 Too Many Requests.
"""
import time
import threading
import urllib.parse
from contextlib import contextmanager
from typing import Iterator, Optional, Dict

MAX_GLOBAL_CONCURRENT = 6
MAX_PER_DOMAIN_CONCURRENT = 2
MIN_DOMAIN_INTERVAL_SECONDS = 0.20

_GLOBAL_SEMAPHORE = threading.BoundedSemaphore(MAX_GLOBAL_CONCURRENT)
_DOMAIN_SEMAPHORES: Dict[str, threading.BoundedSemaphore] = {}
_DOMAIN_LAST_REQUEST: Dict[str, float] = {}
_LOCK = threading.Lock()


def _get_domain(url_or_domain: Optional[str]) -> str:
    if not url_or_domain:
        return "default"
    if "://" in url_or_domain:
        try:
            parsed = urllib.parse.urlparse(url_or_domain)
            return parsed.netloc.lower() or "default"
        except Exception:
            return "default"
    return url_or_domain.lower()


@contextmanager
def throttled_http_request(url_or_domain: Optional[str] = None) -> Iterator[None]:
    """Contexto que limita el número de requests HTTP simultáneos y aplica espaciado por dominio."""
    domain = _get_domain(url_or_domain)
    with _LOCK:
        if domain not in _DOMAIN_SEMAPHORES:
            _DOMAIN_SEMAPHORES[domain] = threading.BoundedSemaphore(MAX_PER_DOMAIN_CONCURRENT)
        domain_sem = _DOMAIN_SEMAPHORES[domain]

    with _GLOBAL_SEMAPHORE:
        with domain_sem:
            # Pacing mínimo entre requests sucesivos al mismo dominio
            with _LOCK:
                last_time = _DOMAIN_LAST_REQUEST.get(domain, 0.0)
                now = time.monotonic()
                elapsed = now - last_time
                if elapsed < MIN_DOMAIN_INTERVAL_SECONDS:
                    time.sleep(MIN_DOMAIN_INTERVAL_SECONDS - elapsed)
                _DOMAIN_LAST_REQUEST[domain] = time.monotonic()
            yield
