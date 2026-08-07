#!/usr/bin/env python3
"""Run a deterministic Repo Coding Runtime benchmark and write JSON/Markdown evidence."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pico.evaluation.evaluator import render_benchmark_markdown, run_fixed_benchmark  # noqa: E402


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Run a deterministic Repo Coding Runtime benchmark.")
    parser.add_argument(
        "--benchmark-path",
        default="benchmarks/v1_5_1_tasks.json",
        help="Benchmark task JSON path.",
    )
    parser.add_argument(
        "--artifact-path",
        default="artifacts/v1_5_1/benchmark.json",
        help="Output JSON artifact path.",
    )
    parser.add_argument(
        "--workspace-root",
        default="artifacts/v1_5_1/workspaces",
        help="Temporary fixture workspace root.",
    )
    parser.add_argument(
        "--markdown-path",
        default="artifacts/v1_5_1/benchmark.md",
        help="Output Markdown report path.",
    )
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    artifact = run_fixed_benchmark(
        benchmark_path=Path(args.benchmark_path),
        artifact_path=Path(args.artifact_path),
        workspace_root=Path(args.workspace_root),
    )
    markdown_path = Path(args.markdown_path)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_benchmark_markdown(artifact) + "\n", encoding="utf-8")
    print(json.dumps(artifact["summary"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if artifact["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
