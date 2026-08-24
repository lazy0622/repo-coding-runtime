import json
from pathlib import Path

from scripts.run_context_recovery_benchmark import build_evidence_summary, render_report


def _fixtures():
    context = {
        "config_count": 2,
        "summary": {
            "avg_raw_prompt_chars": 1000.0,
            "avg_full_prompt_chars": 700.0,
            "avg_prompt_compression_ratio": 0.3,
            "max_prompt_compression_ratio": 0.4,
            "current_request_preserved_rate": 1.0,
        },
    }
    memory = {
        "task_count": 2,
        "runs_per_variant": 4,
        "variants": {
            "memory_on": {
                "repeated_reads": 0,
                "avg_tool_steps": 0.0,
                "correct_rate": 1.0,
            },
            "memory_off": {
                "repeated_reads": 4,
                "avg_tool_steps": 1.0,
                "correct_rate": 1.0,
            },
        },
    }
    recovery_rows = [
        {
            "task_id": "checkpoint_resume_goal",
            "category": "checkpoint_resume",
            "resume_status": "full-valid",
            "resume_succeeded": True,
            "false_accept": False,
        },
        {
            "task_id": "workspace_mismatch_fingerprint",
            "category": "workspace_mismatch",
            "resume_status": "workspace-mismatch",
            "resume_succeeded": False,
            "false_accept": False,
            "workspace_drift_detected": True,
        },
    ]
    recovery = {
        "task_count": 2,
        "variants": {
            "resume_enabled": {
                "rows": recovery_rows,
                "summary": {
                    "resume_success_rate": 0.5,
                    "stale_reanchor_rate": 0.0,
                    "workspace_drift_detection_rate": 1.0,
                    "resume_false_accept_rate": 0.0,
                },
            }
        },
    }
    return context, memory, recovery


def test_context_recovery_summary_has_claim_safe_provenance():
    context, memory, recovery = _fixtures()
    summary = build_evidence_summary(
        context,
        memory,
        recovery,
        captured_at_utc="2026-08-18T00:00:00+00:00",
        git_revision="abc123",
        repetitions={"context": 2, "memory": 2, "recovery": 1},
    )

    assert summary["provenance"]["external_api_calls"] == 0
    assert summary["context"]["sample_runs"] == 4
    assert summary["memory"]["repeated_reads_reduction_rate"] == 1.0
    assert summary["recovery"]["quality"]["resume_status_accuracy"] == 1.0
    assert "SWE-bench solve rate" in " ".join(summary["claim_boundary"])
    json.dumps(summary)
    assert "Interpretation boundary" in render_report(summary)
