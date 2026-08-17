#!/usr/bin/env python3
"""Run a fixed real-repository SWE-bench generation/grade matrix."""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pico.config import load_project_env
from pico.evaluation.swebench_matrix import run_matrix


def build_parser():
    parser = argparse.ArgumentParser(
        description="Run or grade a fixed SWE-bench development selection without outcome filtering."
    )
    parser.add_argument(
        "--selection",
        default="benchmarks/swebench/development-v1-selection.json",
        help="Pre-registered selection JSON or resolved instance manifest.",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="Resolved JSON/JSONL rows produced by prepare_swebench_mini.py.",
    )
    parser.add_argument("--mode", choices=("policy_on", "policy_off", "both"), default="both")
    parser.add_argument("--repetitions", type=int, default=None, help="1 is pilot; use 3 for the fixed formal run.")
    parser.add_argument("--generate-only", action="store_true", help="Generate predictions and do not parse a grade.")
    parser.add_argument("--grade-only", action="store_true", help="Only parse --official-results; do not run an agent.")
    parser.add_argument("--official-results", default=None, help="Official Docker harness JSON/JSONL file or result directory.")
    parser.add_argument("--resume", action="store_true", help="Reuse completed mode/repetition generation summaries.")
    parser.add_argument("--output-dir", default="artifacts/swebench/results/v4-pilot")
    parser.add_argument("--agent-command-json", default=None, help="JSON argv template for the agent command.")
    parser.add_argument("--model", default=None, help="Fixed model name recorded and passed to the agent CLI.")
    parser.add_argument("--temperature", type=float, default=None, help="Fixed temperature recorded and passed to the agent CLI.")
    parser.add_argument("--max-agent-steps", type=int, default=None, help="Fixed agent step budget.")
    parser.add_argument("--timeout", type=int, default=None, help="Per-instance generation timeout in seconds.")
    parser.add_argument("--sandbox-mode", choices=("host", "docker"), default="host")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.generate_only and args.grade_only:
        parser.error("--generate-only and --grade-only are mutually exclusive")
    if args.generate_only and args.official_results:
        parser.error("--generate-only cannot be combined with --official-results")
    if args.grade_only and not args.official_results:
        parser.error("--grade-only requires --official-results from the official grader")
    generate = not args.grade_only
    if generate and not args.agent_command_json:
        parser.error("--agent-command-json is required unless --grade-only is used")
    command = json.loads(args.agent_command_json) if args.agent_command_json else None
    if command is not None and (not isinstance(command, list) or not command):
        parser.error("--agent-command-json must be a non-empty JSON array")
    load_project_env(ROOT)
    os.environ["PYTHONPATH"] = str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")
    result = run_matrix(
        selection_path=args.selection,
        output_dir=args.output_dir,
        agent_command=command,
        mode=args.mode,
        repetitions=args.repetitions,
        manifest_path=args.manifest,
        timeout=args.timeout,
        model_name=args.model,
        temperature=args.temperature,
        max_agent_steps=args.max_agent_steps,
        sandbox_mode=args.sandbox_mode,
        resume=args.resume,
        generate=generate,
        official_results=args.official_results,
        repo_root=ROOT,
    )
    compact = {
        "output_dir": str(Path(args.output_dir).resolve()),
        "evaluation_tier": result["matrix_manifest"]["evaluation_tier"],
        "instance_count": result["matrix_manifest"]["instance_count"],
        "modes": result["matrix_manifest"]["modes"],
        "repetitions": result["matrix_manifest"]["repetitions"],
        "generation_runs": result["generation"]["task_runs"],
        "official_grade_status": (result["official_grade"] or {}).get("official_grade_status", "not_requested"),
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
