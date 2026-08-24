#!/usr/bin/env python3
"""Run the deterministic P0/P1 context and recovery evidence suite.

This benchmark deliberately uses the real runtime objects with scripted model
clients. It does not call a provider, require an API key, or claim model
quality. The output keeps raw ablation artifacts next to a small, versioned
summary under ``benchmarks/reporuntimebench/results``
so the numbers can be reviewed and cited without committing temporary
workspaces.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pico.evaluation.metrics import (  # noqa: E402
    run_context_ablation_v2,
    run_memory_ablation_v2,
    run_recovery_ablation_v2,
)


SCHEMA_VERSION = "repo-coding-runtime-context-recovery-v1"


def _git_revision(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _ratio(numerator: float, denominator: float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _reduction_rate(before: float, after: float) -> float:
    return _ratio(before - after, before)


def _expected_resume_status(task_id: str) -> str:
    if task_id.startswith("checkpoint_resume"):
        return "full-valid"
    if task_id.startswith("partial_stale") or task_id.startswith("partial_success"):
        return "partial-stale"
    if task_id.startswith("workspace_mismatch"):
        return "workspace-mismatch"
    if task_id == "schema_mismatch_version":
        return "schema-mismatch"
    if task_id == "schema_mismatch_missing":
        return "no-checkpoint"
    return ""


def _recovery_quality(recovery: dict) -> dict:
    """Add status-accuracy metrics to the lower-level recovery ablation."""

    enabled_rows = recovery["variants"]["resume_enabled"]["rows"]
    valid_rows = [
        row
        for row in enabled_rows
        if row["category"] in {"checkpoint_resume", "partial_success_recovery"}
    ]
    invalid_rows = [
        row
        for row in enabled_rows
        if row["category"] in {"partial_stale", "workspace_mismatch", "schema_mismatch"}
    ]
    status_matches = [
        row
        for row in enabled_rows
        if row["resume_status"] == _expected_resume_status(row["task_id"])
    ]
    return {
        "enabled_runs": len(enabled_rows),
        "valid_resume_runs": len(valid_rows),
        "valid_resume_success_rate": _ratio(
            sum(1 for row in valid_rows if row["resume_succeeded"]), len(valid_rows)
        ),
        "resume_status_accuracy": _ratio(len(status_matches), len(enabled_rows)),
        "invalid_state_runs": len(invalid_rows),
        "invalid_state_rejection_rate": _ratio(
            sum(1 for row in invalid_rows if not row["false_accept"]), len(invalid_rows)
        ),
        "false_accept_rate": _ratio(
            sum(1 for row in invalid_rows if row["false_accept"]), len(invalid_rows)
        ),
    }


def build_evidence_summary(
    context: dict,
    memory: dict,
    recovery: dict,
    *,
    captured_at_utc: str,
    git_revision: str | None,
    repetitions: dict[str, int],
) -> dict:
    """Build the claim-safe, versioned summary used by the report and tests."""

    context_summary = context["summary"]
    memory_on = memory["variants"]["memory_on"]
    memory_off = memory["variants"]["memory_off"]
    recovery_enabled = recovery["variants"]["resume_enabled"]["summary"]
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "context-memory-recovery-ablation",
        "captured_at_utc": captured_at_utc,
        "provenance": {
            "git_revision": git_revision,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "model_client": "FakeModelClient/scripted deterministic clients",
            "external_api_calls": 0,
            "network_required": False,
            "temperature": None,
        },
        "repetitions": repetitions,
        "context": {
            "config_count": context["config_count"],
            "sample_runs": context["config_count"] * repetitions["context"],
            "baseline_prompt_chars": context_summary["avg_raw_prompt_chars"],
            "reduced_prompt_chars": context_summary["avg_full_prompt_chars"],
            "prompt_chars_reduced": context_summary["avg_raw_prompt_chars"]
            - context_summary["avg_full_prompt_chars"],
            "avg_compression_rate": context_summary["avg_prompt_compression_ratio"],
            "max_compression_rate": context_summary["max_prompt_compression_ratio"],
            "current_request_preserved_rate": context_summary[
                "current_request_preserved_rate"
            ],
        },
        "memory": {
            "task_count": memory["task_count"],
            "sample_runs_per_variant": memory["runs_per_variant"],
            "memory_on": memory_on,
            "memory_off": memory_off,
            "repeated_reads_reduced": memory_off["repeated_reads"]
            - memory_on["repeated_reads"],
            "repeated_reads_reduction_rate": _reduction_rate(
                memory_off["repeated_reads"], memory_on["repeated_reads"]
            ),
            "avg_tool_steps_reduction_rate": _reduction_rate(
                memory_off["avg_tool_steps"], memory_on["avg_tool_steps"]
            ),
            "correct_rate_delta": memory_on["correct_rate"] - memory_off["correct_rate"],
        },
        "recovery": {
            "task_count": recovery["task_count"],
            "sample_runs_per_variant": recovery["task_count"] * repetitions["recovery"],
            "resume_enabled": recovery_enabled,
            "quality": _recovery_quality(recovery),
        },
        "claim_boundary": [
            "These are deterministic runtime/module ablations, not online user data.",
            "The model client is scripted and no provider API was called.",
            "The results do not measure general coding success or SWE-bench solve rate.",
        ],
    }


def render_report(summary: dict) -> str:
    context = summary["context"]
    memory = summary["memory"]
    recovery = summary["recovery"]
    quality = recovery["quality"]
    lines = [
        "# Repo Coding Runtime P0/P1 Context–Memory–Recovery Benchmark",
        "",
        f"- Captured (UTC): `{summary['captured_at_utc']}`",
        f"- Git revision: `{summary['provenance']['git_revision'] or 'unknown'}`",
        f"- Model client: `{summary['provenance']['model_client']}`",
        "- External API calls: `0`; network required: `false`",
        "",
        "## P0 Context and Memory",
        "",
        "| Metric | Result | Sample/definition |",
        "| --- | ---: | --- |",
        f"| Context configurations | {context['config_count']} | {context['sample_runs']} deterministic runs |",
        f"| Average prompt chars | {context['baseline_prompt_chars']:.2f} → {context['reduced_prompt_chars']:.2f} | no reduction → full context governance |",
        f"| Average compression | {context['avg_compression_rate']:.2%} | across fixed context matrix |",
        f"| Maximum compression | {context['max_compression_rate']:.2%} | worst/best fixed configuration |",
        f"| Current request preserved | {context['current_request_preserved_rate']:.2%} | current request suffix check |",
        f"| Memory tasks | {memory['task_count']} | {memory['sample_runs_per_variant']} runs per variant |",
        f"| Follow-up repeated reads | {memory['memory_off']['repeated_reads']} → {memory['memory_on']['repeated_reads']} | memory off → memory on |",
        f"| Repeated-read reduction | {memory['repeated_reads_reduction_rate']:.2%} | same scripted task matrix |",
        f"| Average tool steps | {memory['memory_off']['avg_tool_steps']:.2f} → {memory['memory_on']['avg_tool_steps']:.2f} | memory off → memory on |",
        f"| Memory-on correctness | {memory['memory_on']['correct_rate']:.2%} | verifier is scripted fact equality |",
        "",
        "## P1 Checkpoint / Resume / Workspace Drift",
        "",
        "| Metric | Result | Sample/definition |",
        "| --- | ---: | --- |",
        f"| Recovery scenarios | {recovery['task_count']} | {recovery['sample_runs_per_variant']} runs per variant |",
        f"| Valid resume success | {quality['valid_resume_success_rate']:.2%} | {quality['valid_resume_runs']} valid-state runs |",
        f"| Resume status accuracy | {quality['resume_status_accuracy']:.2%} | expected status per fixed scenario |",
        f"| Stale re-anchor | {recovery['resume_enabled']['stale_reanchor_rate']:.2%} | partial-stale cases |",
        f"| Workspace drift detection | {recovery['resume_enabled']['workspace_drift_detection_rate']:.2%} | workspace-mismatch cases |",
        f"| Invalid-state rejection | {quality['invalid_state_rejection_rate']:.2%} | {quality['invalid_state_runs']} stale/mismatch/schema runs |",
        f"| Stale-state false accept | {quality['false_accept_rate']:.2%} | invalid state accepted as full-valid |",
        "",
        "## Interpretation boundary",
        "",
        "- P0 measures context/memory module behavior under fixed deterministic fixtures; it is not a live-model token or quality benchmark.",
        "- P1 measures whether checkpoint state is accepted, re-anchored, or rejected according to the injected workspace condition.",
        "- Keep these metrics separate from real-provider development results and official SWE-bench grading.",
        "",
    ]
    return "\n".join(lines)


def run(
    output_dir: Path,
    *,
    context_repetitions: int = 5,
    memory_repetitions: int = 5,
    recovery_repetitions: int = 3,
    only: str = "all",
) -> dict:
    output_dir = Path(output_dir)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    context_path = raw_dir / "context-ablation-v2.json"
    memory_path = raw_dir / "memory-ablation-v2.json"
    recovery_path = raw_dir / "recovery-ablation-v2.json"
    if only != "recovery":
        run_context_ablation_v2(context_path, repetitions=context_repetitions)
        run_memory_ablation_v2(memory_path, repetitions=memory_repetitions)
    elif not context_path.is_file() or not memory_path.is_file():
        raise FileNotFoundError(
            "--only recovery requires existing raw/context-ablation-v2.json and "
            "raw/memory-ablation-v2.json"
        )
    run_recovery_ablation_v2(recovery_path, repetitions=recovery_repetitions)

    context = json.loads(context_path.read_text(encoding="utf-8"))
    memory = json.loads(memory_path.read_text(encoding="utf-8"))
    recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
    if only == "recovery":
        context_repetitions = int(context.get("repetitions", context_repetitions))
        memory_repetitions = int(memory.get("repetitions", memory_repetitions))
    recovery_repetitions = int(recovery.get("repetitions", recovery_repetitions))
    captured_at = datetime.now(timezone.utc).isoformat()
    summary = build_evidence_summary(
        context,
        memory,
        recovery,
        captured_at_utc=captured_at,
        git_revision=_git_revision(ROOT),
        repetitions={
            "context": int(context_repetitions),
            "memory": int(memory_repetitions),
            "recovery": int(recovery_repetitions),
        },
    )
    summary_path = output_dir / "context-recovery-summary.json"
    report_path = output_dir / "context-recovery-summary.md"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(render_report(summary), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic P0/P1 context and recovery evaluation.")
    parser.add_argument(
        "--output-dir",
        default="benchmarks/reporuntimebench/results/v4.5-context-recovery",
        help="Directory for the versioned summary and ignored raw artifacts.",
    )
    parser.add_argument("--context-repetitions", type=int, default=5)
    parser.add_argument("--memory-repetitions", type=int, default=5)
    parser.add_argument("--recovery-repetitions", type=int, default=3)
    parser.add_argument("--only", choices=("all", "recovery"), default="all")
    args = parser.parse_args(argv)
    summary = run(
        ROOT / args.output_dir if not Path(args.output_dir).is_absolute() else Path(args.output_dir),
        context_repetitions=args.context_repetitions,
        memory_repetitions=args.memory_repetitions,
        recovery_repetitions=args.recovery_repetitions,
        only=args.only,
    )
    print(json.dumps({"schema_version": summary["schema_version"], "context": summary["context"], "memory": summary["memory"], "recovery": summary["recovery"]}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
