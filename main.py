#!/usr/bin/env python3
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.ui.cli import CotizadorCLI

def main():
    cli = CotizadorCLI()
    try:
        cli.run()
    except KeyboardInterrupt:
        print("\n\nOperación cancelada por el usuario. ¡Hasta luego!")
        sys.exit(0)

if __name__ == "__main__":
    main()
