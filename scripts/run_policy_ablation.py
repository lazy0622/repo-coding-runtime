#!/usr/bin/env python3
"""Run deterministic execution-policy on/off ablation."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pico.evaluation.ablation import run_policy_ablation


def main():
    comparison = run_policy_ablation(
        ROOT / "benchmarks" / "reporuntimebench" / "manifest-v1.json",
        ROOT / "artifacts" / "reporuntimebench-ablation-v1",
    )
    print(json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
