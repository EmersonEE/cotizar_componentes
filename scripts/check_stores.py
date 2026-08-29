#!/usr/bin/env python3
"""Chequeo de salud de los scrapers de las 3 tiendas.

Uso:
  python scripts/check_stores.py                  # tabla legible
  python scripts/check_stores.py --json           # salida JSON (para cron/automatización)
  python scripts/check_stores.py --query "LM358"  # término de búsqueda de prueba
  python scripts/check_stores.py --limit 5        # máx. resultados por búsqueda

Exit code: 0 si todas las tiendas responden OK; 1 si alguna falla (útil en cron).
"""
import sys
import argparse
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.logging_setup import setup_logging  # noqa: E402
from src.health_check import run_store_health_check, print_health_report  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Chequeo de salud de los scrapers de las 3 tiendas.")
    parser.add_argument("--json", action="store_true", help="Salida en JSON (para automatización)")
    parser.add_argument("--query", default="ESP32", help="Término de búsqueda de prueba (default: ESP32)")
    parser.add_argument("--limit", type=int, default=3, help="Máx. resultados a validar por búsqueda")
    parser.add_argument("-v", "--verbose", action="store_true", help="Logs detallados")
    args = parser.parse_args()

    setup_logging(logging.DEBUG if args.verbose else logging.WARNING)

    results = run_store_health_check(query=args.query, search_limit=args.limit)
    print_health_report(results, as_json=args.json)

    ok = all(r["overall_ok"] for r in results)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
