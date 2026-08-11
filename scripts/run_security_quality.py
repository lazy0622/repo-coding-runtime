#!/usr/bin/env python3
"""Run the deterministic security protection/usability quality gate."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pico.evaluation.metrics import run_security_quality_suite


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/security-quality-v3.json")
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    artifact = run_security_quality_suite(args.repetitions, artifact_path=args.output)
    print(json.dumps({key: artifact[key] for key in ("attack_block_rate", "false_block_rate", "secret_leak_rate")}, indent=2))
    return 0 if (
        artifact["attack_block_rate"] == 1.0
        and artifact["false_block_rate"] == 0.0
        and artifact["secret_leak_rate"] == 0.0
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
