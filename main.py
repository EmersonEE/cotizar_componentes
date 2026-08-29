#!/usr/bin/env python3
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.logging_setup import setup_logging
from src.ui.cli import CotizadorCLI

def main():
    # Modo utilitario: chequeo de salud de los scrapers (sin entrar al menú)
    if any(a in sys.argv for a in ("--check-stores", "--health")):
        from src.health_check import run_store_health_check, print_health_report

        results = run_store_health_check()
        print_health_report(results, as_json="--json" in sys.argv)
        sys.exit(0 if all(r["overall_ok"] for r in results) else 1)

    setup_logging()
    cli = CotizadorCLI()
    try:
        cli.run()
    except KeyboardInterrupt:
        print("\n\nOperación cancelada por el usuario. ¡Hasta luego!")
        sys.exit(0)

if __name__ == "__main__":
    main()
