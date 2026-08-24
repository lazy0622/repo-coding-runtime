# Repo Coding Runtime V4 Evidence

- Artifact: `repo-coding-runtime-v4-evidence` / `v4-evidence-v1`
- Captured: `2026-08-24T01:14:36.429948+00:00`
- Git revision: `ac46d56cfb78e5e31fa2d3906113ee4a599951e9`
- Working tree dirty: `True`
- Local verification: `passed`

## Evidence available

- RepoRuntimeBench: `available`; metrics: `{"average_first_edit_step": 1.6666666666666667, "failed": 0, "pass_rate": 1.0, "passed": 24, "patch_generation_rate": 1.0, "read_only_tool_ratio": 0.3902439024390244, "tasks": 24, "verifier_pass_rate": 1.0}`
- Policy ablation: `available`; metrics: `{"delta": {"average_first_edit_step": -0.033333333333333215, "pass_rate": 0.04166666666666663, "passed": 1, "patch_generation_rate": 0.04761904761904767, "read_only_tool_ratio": -0.009756097560975618, "repeated_tool_rejections": 0, "supervisor_intervention_rate": 0.08333333333333333, "verifier_pass_rate": 0.04166666666666663}, "policy_off": {"average_first_edit_step": 1.7, "pass_rate": 0.9583333333333334, "passed": 23, "patch_generation_rate": 0.9523809523809523, "read_only_tool_ratio": 0.4, "repeated_tool_rejections": 1, "supervisor_intervention_rate": 0.0, "total_tasks": 24, "verifier_pass_rate": 0.9583333333333334}, "policy_on": {"average_first_edit_step": 1.6666666666666667, "pass_rate": 1.0, "passed": 24, "patch_generation_rate": 1.0, "read_only_tool_ratio": 0.3902439024390244, "repeated_tool_rejections": 1, "supervisor_intervention_rate": 0.08333333333333333, "total_tasks": 24, "verifier_pass_rate": 1.0}, "scope": "deterministic harness ablation, not live-model quality"}`
- Security quality: `available`; metrics: `{"attack_block_rate": 1.0, "attack_cases": 12, "benign_cases": 6, "false_block_rate": 0.0, "secret_leak_rate": 0.0}`
- Native Tool Protocol: `available`; metrics: `{"failed": 0, "native_call_contract_rate": 1.0, "network_calls": 0, "pass_rate": 1.0, "passed": 6, "schema_conversion_rate": 1.0, "total_cases": 6, "xml_fallback_contract_rate": 1.0}`
- Context/Memory/Recovery: `available`; metrics: `{"avg_compression_rate": 0.34637987751071725, "context_config_count": 12, "context_sample_runs": 24, "current_request_preserved_rate": 1.0, "false_accept_rate": 0.0, "invalid_state_rejection_rate": 1.0, "max_compression_rate": 0.43101745143233455, "memory_avg_tool_steps_reduction_rate": 1.0, "memory_repeated_reads_reduction_rate": 1.0, "memory_task_count": 12, "recovery_task_count": 10, "resume_status_accuracy": 1.0, "valid_resume_success_rate": 1.0, "workspace_drift_detection_rate": 1.0}`
- V4 official SWE-bench grade: `not_run`

## Safe claims

- The runtime has a deterministic harness regression suite and versioned run evidence.
- RepoIndex v3, explicit Host/Docker execution boundaries, policy ablation and failure-preserving evaluation are implemented.
- SWE-bench generation and official grading are separate; no V4 solve-rate claim is made without an official Docker result.

## Raw artifact paths

- `artifacts/reporuntimebench-ablation-v1/ablation.json`
- `artifacts/reporuntimebench-v1/benchmark.json`
- `artifacts/security-quality-v3.json`
- `artifacts/security-quality-v4.json`
- `artifacts/tool-protocol-v1/protocol.json`
- `benchmarks/reporuntimebench/results/v3-evaluation-summary.json`
- `benchmarks/reporuntimebench/results/v3-evaluation-summary.md`
- `benchmarks/reporuntimebench/results/v4-evidence-summary.json`
- `benchmarks/reporuntimebench/results/v4.5-context-recovery/context-recovery-summary.json`
- `benchmarks/reporuntimebench/results/v4.5-context-recovery/context-recovery-summary.md`
- `benchmarks/reporuntimebench/results/v4.5-context-recovery/raw/context-ablation-v2.json`
- `benchmarks/reporuntimebench/results/v4.5-context-recovery/raw/memory-ablation-v2.json`
- `benchmarks/reporuntimebench/results/v4.5-context-recovery/raw/recovery-ablation-v2.json`
- `benchmarks/swebench/development-v1-selection.json`
- `benchmarks/swebench/mini-v1-selection.json`
- `benchmarks/swebench/results/preflight-2026-08-11.json`
- `benchmarks/swebench/results/preflight-2026-08-11.md`
- `docs/architecture/code-intelligence.md`
- `docs/architecture/sandbox.md`
- `docs/evaluation/swebench-methodology.md`

> This report intentionally keeps missing live-model and official-grader results explicit.
