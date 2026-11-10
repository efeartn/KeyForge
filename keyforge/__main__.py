"""Allow running KeyForge via `python -m keyforge`."""

from keyforge.main import main

if __name__ == "__main__":
    raise SystemExit(main())
