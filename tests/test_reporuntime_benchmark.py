from pathlib import Path

from pico.evaluation.evaluator import load_benchmark, run_fixed_benchmark

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "benchmarks" / "reporuntimebench" / "manifest-v1.json"


def test_reporuntimebench_manifest_is_valid():
    benchmark = load_benchmark(MANIFEST, repo_root=ROOT)

    assert len(benchmark["tasks"]) == 24
    assert {task["category"] for task in benchmark["tasks"]} >= {
        "single-file-bugfix",
        "cross-file-bugfix",
        "first-edit-deadline",
        "verification-repair",
    }


def test_reporuntimebench_deterministic_suite_passes(tmp_path):
    artifact = run_fixed_benchmark(
        benchmark_path=MANIFEST,
        artifact_path=tmp_path / "benchmark.json",
        workspace_root=tmp_path / "workspaces",
        model_name="FakeModelClient",
        model_version="scripted-policy-v1",
    )

    assert artifact["summary"]["total_tasks"] == 24
    assert artifact["summary"]["failed"] == 0
    assert artifact["execution_metrics"]["patch_generation_rate"] == 1.0
    assert artifact["execution_metrics"]["average_first_edit_step"] > 0
    assert artifact["execution_metrics"]["supervisor_intervention_rate"] > 0
    assert any(row["execution_policy"]["repeated_tool_rejections"] == 1 for row in artifact["rows"])
    assert any(row["hidden_verifier_configured"] for row in artifact["rows"])
    assert all(row["hidden_verifier_passed"] for row in artifact["rows"])
