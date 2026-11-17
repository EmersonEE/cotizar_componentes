"""Gestor centralizado de cliente HTTP persistente con pool de conexiones.

Reutiliza sockets TCP y negociaciones TLS hacia las tiendas locales soportadas,
evitando el overhead de reconexión constante durante la metabúsqueda y evitando
bloqueos por fingerprinting TLS en tiendas alojadas en Cloudflare/Shopify.
"""
import atexit
import threading
from typing import Optional, Any
import requests
from requests.adapters import HTTPAdapter

_CLIENT: Optional['HttpClient'] = None
_CLIENT_LOCK = threading.Lock()


class HttpClient:
    """Cliente HTTP con pool de conexiones y compatibilidad de interfaz con httpx."""

    def __init__(self, timeout: float = 12.0):
        self.default_timeout = timeout
        self.session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=20,
            pool_maxsize=50,
            max_retries=0,
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self._is_closed = False

    def get(
        self,
        url: str,
        headers: Optional[dict] = None,
        timeout: Optional[float] = None,
        follow_redirects: bool = True,
        **kwargs: Any
    ) -> requests.Response:
        t = timeout if timeout is not None else self.default_timeout
        return self.session.get(
            url,
            headers=headers,
            timeout=t,
            allow_redirects=follow_redirects,
            **kwargs
        )

    def close(self) -> None:
        self.session.close()
        self._is_closed = True

    @property
    def is_closed(self) -> bool:
        return self._is_closed


def get_http_client(timeout: float = 12.0) -> HttpClient:
    """Devuelve la instancia compartida de HttpClient con límites y pooling configurados."""
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None or _CLIENT.is_closed:
            _CLIENT = HttpClient(timeout=timeout)
        return _CLIENT


def close_http_client() -> None:
    """Cierra limpiamente el cliente HTTP y libera los sockets subyacentes."""
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is not None and not _CLIENT.is_closed:
            try:
                _CLIENT.close()
            except Exception:
                pass
            _CLIENT = None


atexit.register(close_http_client)
