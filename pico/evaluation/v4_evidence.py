"""Build a versioned, claim-safe evidence summary for the V4 release.

The evidence pack deliberately records missing live-model or official-grader
artifacts as explicit states.  It never infers SWE-bench resolution from a
process exit code, a non-empty patch, or a local verifier result.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


V4_EVIDENCE_SCHEMA_VERSION = "v4-evidence-v1"


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


def _sha256(path):
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _relative(root, path):
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return str(path)


def _run_git(root, *args):
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_provenance(root):
    root = Path(root).resolve()
    try:
        diff = subprocess.run(
            ["git", "diff", "--binary", "--no-ext-diff"],
            cwd=str(root),
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        diff = None
    digest = hashlib.sha256(diff.stdout).hexdigest() if diff and diff.returncode == 0 else None
    return {
        "git_revision": _run_git(root, "rev-parse", "HEAD") or None,
        "working_tree_dirty": bool(digest),
        "working_tree_diff_sha256": digest,
    }


def _selection_record(root, relative_path):
    path = Path(root) / relative_path
    return {
        "path": relative_path,
        "sha256": _sha256(path) if path.is_file() else None,
        "status": "present" if path.is_file() else "missing",
    }


def _first_existing(root, candidates):
    for relative_path in candidates:
        path = Path(root) / relative_path
        if path.is_file():
            return relative_path, _read_json(path)
    return None, None


def _deterministic_metrics(data):
    if not isinstance(data, dict):
        return {}
    if isinstance(data.get("summary"), dict):
        summary = data["summary"]
        metrics = data.get("execution_metrics", {})
        return {
            "tasks": summary.get("total_tasks"),
            "passed": summary.get("passed"),
            "failed": summary.get("failed"),
            "pass_rate": summary.get("pass_rate"),
            "verifier_pass_rate": summary.get("verifier_pass_rate"),
            "patch_generation_rate": metrics.get("patch_generation_rate"),
            "average_first_edit_step": metrics.get("average_first_edit_step"),
            "read_only_tool_ratio": metrics.get("read_only_tool_ratio"),
        }
    value = data.get("deterministic")
    return dict(value) if isinstance(value, dict) else {}


def _policy_metrics(data):
    if not isinstance(data, dict):
        return {}
    if isinstance(data.get("baseline"), dict) and isinstance(data.get("enhanced"), dict):
        return {
            "scope": data.get("scope") or data.get("method"),
            "policy_off": data["baseline"],
            "policy_on": data["enhanced"],
            "delta": data.get("delta", {}),
        }
    value = data.get("policy_ablation")
    return dict(value) if isinstance(value, dict) else {}


def _security_metrics(data):
    if not isinstance(data, dict):
        return {}
    value = data.get("security_quality", data)
    if not isinstance(value, dict):
        return {}
    return {
        key: value.get(key)
        for key in (
            "repetitions",
            "attack_cases",
            "benign_cases",
            "attack_block_rate",
            "false_block_rate",
            "secret_leak_rate",
        )
        if key in value
    }


def _tool_protocol_metrics(data):
    if not isinstance(data, dict):
        return {}
    summary = data.get("summary") or {}
    metrics = data.get("metrics") or {}
    return {
        "total_cases": summary.get("total_cases"),
        "passed": summary.get("passed"),
        "failed": summary.get("failed"),
        "pass_rate": summary.get("pass_rate"),
        "native_call_contract_rate": metrics.get("native_call_contract_rate"),
        "xml_fallback_contract_rate": metrics.get("xml_fallback_contract_rate"),
        "schema_conversion_rate": metrics.get("schema_conversion_rate"),
        "network_calls": metrics.get("network_calls"),
    }


def _load_optional_artifact(root, candidates, summarizer):
    path, data = _first_existing(root, candidates)
    if path is None:
        return {"status": "not_available", "source": candidates[0], "metrics": {}}
    return {
        "status": "available",
        "source": path,
        "metrics": summarizer(data),
    }


def _latest_official_grade(root):
    candidates = sorted(
        path.relative_to(root).as_posix()
        for path in (Path(root) / "artifacts" / "swebench" / "results").glob(
            "v4-*/official_grade_summary.json"
        )
        if path.is_file()
    )
    if not candidates:
        return {
            "status": "not_run",
            "source": "artifacts/swebench/results/v4-*/official_grade_summary.json",
            "official_resolved": None,
            "official_resolved_rate": None,
        }
    source = candidates[-1]
    data = _read_json(Path(root) / source) or {}
    return {
        "status": data.get("official_grade_status", "failed"),
        "source": source,
        "official_resolved": data.get("official_resolved"),
        "official_resolved_rate": data.get("official_resolved_rate"),
        "official_failed_instances": data.get("official_failed_instances", []),
        "missing_instances": data.get("missing_instances", []),
    }


def _raw_paths(root):
    candidates = [
        "artifacts/reporuntimebench-v1/benchmark.json",
        "artifacts/reporuntimebench-ablation-v1/ablation.json",
        "artifacts/security-quality-v4.json",
        "artifacts/tool-protocol-v1/protocol.json",
        "artifacts/security-quality-v3.json",
        "benchmarks/reporuntimebench/results/v3-evaluation-summary.json",
        "benchmarks/reporuntimebench/results/v3-evaluation-summary.md",
        "benchmarks/swebench/results/preflight-2026-08-11.json",
        "benchmarks/swebench/results/preflight-2026-08-11.md",
        "benchmarks/swebench/development-v1-selection.json",
        "benchmarks/swebench/mini-v1-selection.json",
        "docs/evaluation/swebench-methodology.md",
        "docs/architecture/sandbox.md",
        "docs/architecture/code-intelligence.md",
    ]
    for directory in (Path(root) / "artifacts",):
        if directory.is_dir():
            for path in directory.glob("**/v4-evidence-summary.json"):
                candidates.append(_relative(root, path))
            for path in directory.glob("**/official_grade_summary.json"):
                candidates.append(_relative(root, path))
    return sorted({path for path in candidates if (Path(root) / path).exists()})


def build_v4_evidence(root, verification=None, captured_at=None):
    """Build a path-normalized V4 evidence object from local artifacts."""

    root = Path(root).resolve()
    verification = dict(verification or {})
    deterministic = _load_optional_artifact(
        root,
        [
            "artifacts/reporuntimebench-v1/benchmark.json",
            "benchmarks/reporuntimebench/results/v3-evaluation-summary.json",
        ],
        _deterministic_metrics,
    )
    policy = _load_optional_artifact(
        root,
        [
            "artifacts/reporuntimebench-ablation-v1/ablation.json",
            "benchmarks/reporuntimebench/results/v3-evaluation-summary.json",
        ],
        _policy_metrics,
    )
    security = _load_optional_artifact(
        root,
        [
            "artifacts/security-quality-v4.json",
            "artifacts/security-quality-v3.json",
            "benchmarks/reporuntimebench/results/v3-evaluation-summary.json",
        ],
        _security_metrics,
    )
    tool_protocol = _load_optional_artifact(
        root,
        ["artifacts/tool-protocol-v1/protocol.json"],
        _tool_protocol_metrics,
    )
    captured_at = captured_at or datetime.now(timezone.utc).isoformat()
    evidence = {
        "artifact_type": "repo-coding-runtime-v4-evidence",
        "schema_version": V4_EVIDENCE_SCHEMA_VERSION,
        "captured_at_utc": captured_at,
        **_git_provenance(root),
        "selection_files": {
            "development": _selection_record(root, "benchmarks/swebench/development-v1-selection.json"),
            "mini": _selection_record(root, "benchmarks/swebench/mini-v1-selection.json"),
        },
        "model_provider": {
            "deterministic": {
                "provider": "scripted",
                "model": "FakeModelClient",
                "version": "scripted-policy-v1",
            },
            "historical_deepseek": {
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "scope": "six fixed local development tasks, one run per condition",
                "status": "historical_v3_evidence",
            },
            "swebench_v4": {
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "status": "not_run_until_explicitly_started",
            },
        },
        "execution_policy": {
            "default": "on",
            "ablation_modes": ["policy_off", "policy_on"],
            "comparison_contract": "same selection, model, temperature, step budget, timeout and sandbox",
        },
        "sandbox": {
            "default_mode": "host",
            "docker_mode": "explicit_only",
            "docker_network": "none",
            "docker_non_root": True,
            "docker_read_only_root": True,
            "host_fallback_when_docker_selected": False,
            "official_swebench_grader": "separate_linux_docker_step",
        },
        "repetitions": {
            "deterministic_reporuntimebench": 1,
            "historical_deepseek_development": 1,
            "swebench_pilot": 1,
            "swebench_formal_target": 3,
            "swebench_formal_completed": False,
        },
        "benchmark_evidence": {
            "reporuntimebench": deterministic,
            "policy_ablation": policy,
            "security_quality": security,
            "tool_protocol": tool_protocol,
        },
        "official_evaluation": {
            "v4": _latest_official_grade(root),
            "historical_preflight": {
                "status": "recorded_separately",
                "source": "benchmarks/swebench/results/preflight-2026-08-11.json",
                "official_resolved": 0,
                "official_instances": 1,
                "claim": "historical preflight only; not a general solve rate",
            },
        },
        "local_verification": {
            "status": verification.get("status", "not_run"),
            "commands": list(verification.get("commands", [])),
        },
        "raw_artifact_paths": _raw_paths(root),
        "claim_boundary": (
            "Generation metrics and deterministic harness results are not official "
            "SWE-bench solve rate. Only official Docker harness output can populate "
            "official_resolved."
        ),
    }
    return evidence


def render_v4_evidence(evidence):
    """Render a compact human-readable V4 evidence report."""

    deterministic = evidence["benchmark_evidence"]["reporuntimebench"]
    policy = evidence["benchmark_evidence"]["policy_ablation"]
    security = evidence["benchmark_evidence"]["security_quality"]
    tool_protocol = evidence["benchmark_evidence"]["tool_protocol"]
    official = evidence["official_evaluation"]["v4"]
    lines = [
        "# Repo Coding Runtime V4 Evidence",
        "",
        f"- Artifact: `{evidence['artifact_type']}` / `{evidence['schema_version']}`",
        f"- Captured: `{evidence['captured_at_utc']}`",
        f"- Git revision: `{evidence.get('git_revision') or 'unavailable'}`",
        f"- Working tree dirty: `{evidence.get('working_tree_dirty')}`",
        f"- Local verification: `{evidence['local_verification']['status']}`",
        "",
        "## Evidence available",
        "",
        f"- RepoRuntimeBench: `{deterministic['status']}`; metrics: `{json.dumps(deterministic['metrics'], ensure_ascii=False, sort_keys=True)}`",
        f"- Policy ablation: `{policy['status']}`; metrics: `{json.dumps(policy['metrics'], ensure_ascii=False, sort_keys=True)}`",
        f"- Security quality: `{security['status']}`; metrics: `{json.dumps(security['metrics'], ensure_ascii=False, sort_keys=True)}`",
        f"- Native Tool Protocol: `{tool_protocol['status']}`; metrics: `{json.dumps(tool_protocol['metrics'], ensure_ascii=False, sort_keys=True)}`",
        f"- V4 official SWE-bench grade: `{official['status']}`",
        "",
        "## Safe claims",
        "",
        "- The runtime has a deterministic harness regression suite and versioned run evidence.",
        "- RepoIndex v3, explicit Host/Docker execution boundaries, policy ablation and failure-preserving evaluation are implemented.",
        "- SWE-bench generation and official grading are separate; no V4 solve-rate claim is made without an official Docker result.",
        "",
        "## Raw artifact paths",
        "",
    ]
    lines.extend(f"- `{path}`" for path in evidence["raw_artifact_paths"])
    lines.extend(["", "> This report intentionally keeps missing live-model and official-grader results explicit.", ""])
    return "\n".join(lines)


def write_v4_evidence(root, output_path, markdown_path=None, verification=None):
    output_path = Path(output_path)
    evidence = build_v4_evidence(root, verification=verification)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if markdown_path is None:
        markdown_path = output_path.with_suffix(".md")
    markdown_path = Path(markdown_path)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_v4_evidence(evidence), encoding="utf-8")
    return evidence


__all__ = [
    "V4_EVIDENCE_SCHEMA_VERSION",
    "build_v4_evidence",
    "render_v4_evidence",
    "write_v4_evidence",
]
