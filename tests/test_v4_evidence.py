import json
import subprocess

from pico.evaluation.v4_evidence import build_v4_evidence, render_v4_evidence


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def test_v4_evidence_is_versioned_path_normalized_and_claim_safe(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "evidence@example.com")
    git(tmp_path, "config", "user.name", "Evidence")
    selection_dir = tmp_path / "benchmarks" / "swebench"
    selection_dir.mkdir(parents=True)
    (selection_dir / "development-v1-selection.json").write_text("{}", encoding="utf-8")
    (selection_dir / "mini-v1-selection.json").write_text("{}", encoding="utf-8")
    result_dir = tmp_path / "benchmarks" / "reporuntimebench" / "results"
    result_dir.mkdir(parents=True)
    (result_dir / "v3-evaluation-summary.json").write_text(
        json.dumps(
            {
                "deterministic": {"tasks": 24, "passed": 24, "pass_rate": 1.0},
                "policy_ablation": {"off": {"passed": 23}, "on": {"passed": 24}},
                "security_quality": {"attack_block_rate": 1.0, "false_block_rate": 0.0, "secret_leak_rate": 0.0},
            }
        ),
        encoding="utf-8",
    )
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "evidence fixture")

    evidence = build_v4_evidence(
        tmp_path,
        verification={"status": "passed", "commands": ["pytest"]},
        captured_at="2026-08-14T00:00:00+00:00",
    )

    assert evidence["schema_version"] == "v4-evidence-v1"
    assert evidence["selection_files"]["development"]["sha256"]
    assert evidence["benchmark_evidence"]["reporuntimebench"]["metrics"]["passed"] == 24
    assert evidence["official_evaluation"]["v4"]["status"] == "not_run"
    assert all(str(tmp_path) not in path for path in evidence["raw_artifact_paths"])
    assert "official" in render_v4_evidence(evidence)
