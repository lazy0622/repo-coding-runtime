"""Generate SWE-bench predictions from real repository checkouts."""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pico.config import load_project_env
from pico.evaluation.swebench import (
    SWEbenchAdapter,
    load_instances,
    official_evaluation_command,
)


def main():
    load_project_env(ROOT)
    os.environ["PYTHONPATH"] = str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--agent-command-json", required=True, help='JSON argv, e.g. ["repo","--prompt-file","{prompt_file}"]')
    parser.add_argument("--model-name", default="repo-coding-runtime")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--dataset", default="SWE-bench/SWE-bench_Lite")
    parser.add_argument("--limit", type=int, default=0, help="Use the first N pinned instances; 0 means all.")
    parser.add_argument("--repetitions", type=int, default=1)
    args = parser.parse_args()
    command = json.loads(args.agent_command_json)
    if not isinstance(command, list) or not command:
        parser.error("--agent-command-json must be a non-empty JSON array")
    instances = load_instances(args.manifest)
    if args.limit > 0:
        instances = instances[: args.limit]
    output_root = Path(args.output).resolve()
    repetitions = max(1, int(args.repetitions))
    summaries = []
    for index in range(repetitions):
        repetition_dir = output_root / f"repetition-{index + 1:02d}"
        adapter = SWEbenchAdapter(
            repetition_dir,
            model_name=args.model_name,
            cache_dir=output_root / "repo-cache",
        )
        summary = adapter.run(instances, command, timeout=args.timeout)
        summary["repetition"] = index + 1
        summary["official_evaluation_command"] = official_evaluation_command(summary["predictions_path"], args.dataset)
        summaries.append(summary)
    runs = [run for summary in summaries for run in summary["runs"]]
    first_edit_steps = [run["first_edit_step"] for run in runs if run["first_edit_step"] > 0]
    aggregate = {
        "artifact_type": "swebench-generation-experiment-v1",
        "instance_count": len(instances),
        "repetitions": repetitions,
        "task_runs": len(runs),
        "agent_completion_rate": sum(run["agent_completed"] for run in runs) / len(runs) if runs else 0.0,
        "non_empty_patch_rate": sum(run["patch_bytes"] > 0 for run in runs) / len(runs) if runs else 0.0,
        "average_tool_steps": sum(run["tool_steps"] for run in runs) / len(runs) if runs else 0.0,
        "average_first_edit_step": sum(first_edit_steps) / len(first_edit_steps) if first_edit_steps else 0.0,
        "summaries": summaries,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "experiment-summary.json").write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(aggregate, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
