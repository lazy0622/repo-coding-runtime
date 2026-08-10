#!/usr/bin/env python3
"""Run the fixed smoke or development suite with the configured DeepSeek provider."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pico.config import load_project_env, provider_env
from pico.evaluation.evaluator import (
    render_benchmark_markdown,
    run_fixed_benchmark,
)
from pico.providers.clients import AnthropicCompatibleModelClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=("smoke", "development"), default="smoke")
    parser.add_argument("--policy", choices=("on", "off"), default="on")
    args = parser.parse_args()
    load_project_env(ROOT)
    api_key = provider_env("PICO_DEEPSEEK_API_KEY", ("DEEPSEEK_API_KEY",))
    if not api_key:
        raise SystemExit("PICO_DEEPSEEK_API_KEY or DEEPSEEK_API_KEY is required")
    model = provider_env("PICO_DEEPSEEK_MODEL", ("DEEPSEEK_MODEL",), "deepseek-v4-pro")
    base_url = provider_env(
        "PICO_DEEPSEEK_API_BASE",
        ("DEEPSEEK_API_BASE",),
        "https://api.deepseek.com/anthropic",
    )

    def factory(task, workspace):
        del task, workspace
        return AnthropicCompatibleModelClient(
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=0.0,
            timeout=300,
        )

    manifest_name = "live-smoke-v1.json" if args.suite == "smoke" else "live-development-v1.json"
    run_name = f"deepseek-{args.suite}-policy-{args.policy}"
    artifact = run_fixed_benchmark(
        benchmark_path=ROOT / "benchmarks" / "reporuntimebench" / manifest_name,
        artifact_path=ROOT / "artifacts" / "reporuntimebench-live" / f"{run_name}.json",
        workspace_root=ROOT / "artifacts" / "reporuntimebench-live" / "workspaces" / run_name,
        model_name="deepseek",
        model_version=model,
        max_new_tokens=1024,
        model_client_factory=factory,
        execution_policy_override={"enabled": args.policy == "on"},
    )
    report_path = ROOT / "artifacts" / "reporuntimebench-live" / f"{run_name}.md"
    report_path.write_text(render_benchmark_markdown(artifact) + "\n", encoding="utf-8")
    print(json.dumps({**artifact["summary"], **artifact["execution_metrics"]}, ensure_ascii=False, indent=2))
    return 0 if not artifact["summary"]["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
