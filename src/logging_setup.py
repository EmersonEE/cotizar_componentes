"""Configuración centralizada de logging para CLI y Web."""
import logging

_LOGGING_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> None:
    """
    Configures the root logger once with a consistent format on stderr.
    Safe to call from main.py (CLI), app.py (Streamlit) and scripts.
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
    # Reemplaza handlers previos (p.ej. los que Streamlit ya haya instalado)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)

    _LOGGING_CONFIGURED = True
