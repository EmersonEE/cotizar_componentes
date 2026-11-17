"""Configuración centralizada de logging para el CLI y scripts."""
import logging

_LOGGING_CONFIGURED = False

# Loggers de terceros cuyo INFO (requests HTTP, etc.) ensucia la terminal
NOISY_LOGGERS = ("httpx", "httpcore", "urllib3", "charset_normalizer", "requests")


def setup_logging(level: int = logging.INFO) -> None:
    """
    Configures the root logger once with a consistent format on stderr.
    Safe to call from main.py (CLI) and scripts.
    Los loggers de terceros ruidosos (httpx, httpcore, urllib3) se suben a
    WARNING para que la terminal solo muestre lo relevante del cotizador.
    """
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    # Reemplaza handlers previos si existen
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)

    # Silenciar el ruido de requests de terceros (ej. "HTTP Request: GET ... 200 OK")
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    _LOGGING_CONFIGURED = True
