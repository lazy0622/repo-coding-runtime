import json
from pathlib import Path

from pico.evaluation.evaluator import load_benchmark, render_benchmark_markdown, run_fixed_benchmark


BENCHMARK_PATH = Path("benchmarks/v1_5_1_tasks.json")


def test_v1_5_1_benchmark_covers_plan_verification_and_replay(tmp_path):
    benchmark = load_benchmark(BENCHMARK_PATH)

    assert len(benchmark["tasks"]) == 3
    assert {task["category"] for task in benchmark["tasks"]} == {
        "plan-verify",
        "verification-retry",
        "replay-evidence",
    }

    artifact = run_fixed_benchmark(
        benchmark_path=BENCHMARK_PATH,
        artifact_path=tmp_path / "v1_5_1-benchmark.json",
        workspace_root=tmp_path / "workspaces",
    )

    assert artifact["summary"]["total_tasks"] == 3
    assert artifact["summary"]["passed"] == 3
    assert artifact["summary"]["pass_rate"] == 1.0

    plan_row = next(row for row in artifact["rows"] if row["id"] == "plan_verify_status")
    retry_row = next(row for row in artifact["rows"] if row["id"] == "verification_retry")
    replay_row = next(row for row in artifact["rows"] if row["id"] == "plan_replay_evidence")

    assert plan_row["verification"]["passed"] is True
    assert plan_row["verification_attempts"] == 1
    assert retry_row["verification_attempts"] == 2
    assert replay_row["verification"]["status"] == "passed"

    persisted = json.loads((tmp_path / "v1_5_1-benchmark.json").read_text(encoding="utf-8"))
    markdown = render_benchmark_markdown(persisted)
    assert "plan_verify_status" in markdown
    assert "verification_retry" in markdown
    assert "Pass rate: 100.0%" in markdown
