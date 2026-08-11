#!/usr/bin/env python3
"""Run the deterministic RepoRuntimeBench v1 starter set."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pico.evaluation.evaluator import (
    render_benchmark_markdown,
    run_fixed_benchmark,
)


def main():
    artifact = run_fixed_benchmark(
        benchmark_path=ROOT / "benchmarks" / "reporuntimebench" / "manifest-v1.json",
        artifact_path=ROOT / "artifacts" / "reporuntimebench-v1" / "benchmark.json",
        workspace_root=ROOT / "artifacts" / "reporuntimebench-v1" / "workspaces",
        model_name="FakeModelClient",
        model_version="scripted-policy-v1",
    )
    report_path = ROOT / "artifacts" / "reporuntimebench-v1" / "benchmark.md"
    report_path.write_text(render_benchmark_markdown(artifact) + "\n", encoding="utf-8")
    print(json.dumps({**artifact["summary"], **artifact["execution_metrics"]}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not artifact["summary"]["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
