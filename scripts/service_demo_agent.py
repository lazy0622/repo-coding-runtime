"""Deterministic demo executor; never count this as a model repair result."""

import sys
from pathlib import Path

workspace = Path(sys.argv[1]).resolve(strict=True)
target = workspace / "calculator.py"
target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
print("Deterministic fixture patch created")
