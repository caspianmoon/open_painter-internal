"""Entry point for the opt-in Material Director use-case benchmark."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests/benchmarks/material_director"))
from runner import main

if __name__ == "__main__":
    raise SystemExit(main())
