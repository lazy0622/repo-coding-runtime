"""Policy on/off evaluation for the repository coding runtime."""

import json
from pathlib import Path

from .evaluator import render_benchmark_markdown, run_fixed_benchmark


def _metric_snapshot(artifact):
    summary = dict(artifact.get("summary", {}) or {})
    execution = dict(artifact.get("execution_metrics", {}) or {})
    return {
        "total_tasks": int(summary.get("total_tasks", 0)),
        "passed": int(summary.get("passed", 0)),
        "pass_rate": float(summary.get("pass_rate", 0.0)),
        "verifier_pass_rate": float(summary.get("verifier_pass_rate", 0.0)),
        "patch_generation_rate": float(execution.get("patch_generation_rate", 0.0)),
        "average_first_edit_step": float(execution.get("average_first_edit_step", 0.0)),
        "read_only_tool_ratio": float(execution.get("read_only_tool_ratio", 0.0)),
        "supervisor_intervention_rate": float(execution.get("supervisor_intervention_rate", 0.0)),
        "repeated_tool_rejections": int(execution.get("repeated_tool_rejections", 0)),
    }


def compare_policy_artifacts(baseline, enhanced):
    baseline_metrics = _metric_snapshot(baseline)
    enhanced_metrics = _metric_snapshot(enhanced)
    numeric_keys = set(baseline_metrics) & set(enhanced_metrics)
    delta = {
        key: enhanced_metrics[key] - baseline_metrics[key]
        for key in sorted(numeric_keys)
        if key != "total_tasks"
    }
    return {
        "schema_version": 1,
        "method": "same manifest and scripted model outputs; execution policy disabled vs enabled",
        "scope": "deterministic harness ablation, not live-model quality",
        "baseline": baseline_metrics,
        "enhanced": enhanced_metrics,
        "delta": delta,
    }


def render_policy_ablation_markdown(comparison):
    before = comparison["baseline"]
    after = comparison["enhanced"]
    return "\n".join(
        [
            "# Execution Policy Ablation",
            "",
            f"- Method: {comparison['method']}",
            f"- Scope: {comparison['scope']}",
            f"- Pass rate: {before['pass_rate']:.1%} -> {after['pass_rate']:.1%}",
            f"- Verifier pass rate: {before['verifier_pass_rate']:.1%} -> {after['verifier_pass_rate']:.1%}",
            f"- Patch generation rate: {before['patch_generation_rate']:.1%} -> {after['patch_generation_rate']:.1%}",
            f"- Average first edit step: {before['average_first_edit_step']:.2f} -> {after['average_first_edit_step']:.2f}",
            f"- Read-only tool ratio: {before['read_only_tool_ratio']:.1%} -> {after['read_only_tool_ratio']:.1%}",
            "",
            "> Fixed fixtures and scripted outputs isolate runtime behavior. Do not report these values as SWE-bench or live-model solve rates.",
        ]
    )


def run_policy_ablation(benchmark_path, output_dir, workspace_root=None):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace_root = Path(workspace_root or output_dir / "workspaces")
    baseline = run_fixed_benchmark(
        benchmark_path=benchmark_path,
        artifact_path=output_dir / "policy-off.json",
        workspace_root=workspace_root / "policy-off",
        model_name="FakeModelClient",
        model_version="scripted-policy-off",
        execution_policy_override={"enabled": False},
    )
    enhanced = run_fixed_benchmark(
        benchmark_path=benchmark_path,
        artifact_path=output_dir / "policy-on.json",
        workspace_root=workspace_root / "policy-on",
        model_name="FakeModelClient",
        model_version="scripted-policy-on",
        execution_policy_override={"enabled": True},
    )
    comparison = compare_policy_artifacts(baseline, enhanced)
    (output_dir / "policy-off.md").write_text(render_benchmark_markdown(baseline) + "\n", encoding="utf-8")
    (output_dir / "policy-on.md").write_text(render_benchmark_markdown(enhanced) + "\n", encoding="utf-8")
    (output_dir / "ablation.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "ablation.md").write_text(render_policy_ablation_markdown(comparison) + "\n", encoding="utf-8")
    return comparison
