# Repo Coding Runtime Review Pack

## Project pitch

Repo Coding Runtime is a lightweight local Coding Agent Runtime for repository-grounded engineering tasks. It wraps a model with workspace context, explicit tools, state tracking, memory, run artifacts, and benchmark evidence. The implementation package remains `pico`.

## Architecture map

- `pico.cli` wires configuration, provider clients, workspace context, and the runtime.
- `pico.runtime.Pico` coordinates the agent control surface.
- `pico.context_manager` builds bounded model context from prefix, memory, history, and the current request.
- `pico.tools` defines the explicit tool allowlist used by the runtime.
- `pico.repo_index` provides a fingerprint-aware Python AST index for outlines, symbol/reference search, and dependency edges.
- `pico.patching` provides strict unified-diff preflight, application, and guarded rollback backups.
- `pico.task_graph` validates V2 dependency graphs and failure propagation.
- `pico.subagents` runs bounded read-only child runtimes with per-child artifacts.
- `pico.run_store` writes per-run artifacts for review and replay.

## Benchmark evidence

Benchmark runs should preserve reproducibility metadata, task rows, summary counts, and failure categories so reviewers can distinguish runtime regressions from task or provider failures.

The V1.5.1 deterministic benchmark can be run from the repository root:

```bash
python scripts/run_benchmark.py
```

It writes a JSON artifact and a human-readable Markdown report under
`artifacts/v1_5_1/`. The task set covers Plan–Execute–Verify, bounded repair
after a failed verification, and replayable trace evidence.

Three review demos are available with:

```bash
python scripts/run_v1_5_1_demos.py
```

The demos cover a successful plan/verification run, a destructive command
blocked by the safety policy, and a read-only run with replay artifacts.

## V1.6 review points

V1.6 keeps repository navigation and code modification inside the existing
`ToolRegistry` and `ToolGateway` boundary. Reviewers can inspect:

- AST-backed `get_file_outline`, `find_symbol`, `find_references`, and
  `get_dependency_graph` results before the agent reads broad source ranges.
- `preview_diff` output before a risky edit.
- `apply_patch` metadata and `.pico/patches/<backup_id>.json` for the exact
  files and content fingerprint used by a rollback.
- `rollback_patch` conflict refusal when a file changed after the patch.

## V2 review points

V2 upgrades the experimental delegate path into an explicit Supervisor boundary:

- `TaskGraph` is validated before execution, including unknown dependencies and cycles.
- `run_task_graph` only exposes read-only tools to children and limits the graph to six tasks.
- Child runs receive separate session/run directories; the parent trace records child lifecycle events.
- A failed task blocks dependent tasks instead of allowing them to consume incomplete evidence.
- Git worktree isolation is optional and failures fall back to a recorded read-only mode; there is no hidden destructive cleanup.

## V2.1–V2.4 review points

- `EvidenceBundle` turns child output into inspectable claims, file/line evidence, risks, recommendations, and confidence; the Supervisor returns a deduplicated aggregate and stores the full graph state.
- `GraphTask` records attempts, per-task timeout budgets, retry history, and recovery on resume. A checkpoint can requeue an interrupted task without rerunning completed dependencies.
- `run_coding_workflow` demonstrates the complete coding-agent loop: research first, preview and apply a strict patch, run an explicit verifier, then guarded rollback on failure.
- The workflow does not hide authority: the caller supplies the patch and verifier, child agents stay read-only, and rollback refuses to overwrite files changed after the patch.

## V4 release review points

- `RepoIndex v3` adds bounded, confidence-bearing Python call/impact evidence;
  unresolved dynamic calls remain diagnostics instead of being presented as
  exact dependencies.
- `HostExecutionBackend` and `DockerExecutionBackend` share an execution
  contract. Docker is explicit, network-isolated by default, non-root and
  resource-bounded; selecting it cannot silently fall back to Host.
- The fixed SWE-bench matrix separates `generation_metrics` from
  `official_resolved`, fixes the instance list and budget, supports policy
  ablation, and preserves failures under `failures/`.
- CI runs the unit/code-intelligence/sandbox contracts and deterministic
  RepoRuntimeBench. Docker integration, live DeepSeek calls and the official
  grader remain explicit/manual because they require environment, secret,
  cost and time budgets.
- Native Tool Calling is provider-adapted at the boundary: OpenAI Responses
  uses function parameters, Anthropic Messages uses `input_schema`, and both
  become the same runtime `ToolCall`. XML remains the deterministic/text-only
  fallback, so the native path does not bypass ToolGateway or approvals.

The versioned release evidence is generated by:

```bash
python scripts/write_v4_evidence.py --verification-status passed
```

It writes `benchmarks/reporuntimebench/results/v4-evidence-summary.json` and
the Markdown companion with normalized paths, selection hashes, Git
provenance, benchmark sources and the official-grade claim boundary.

## Resume and failure review

`--resume` on the SWE-bench matrix reuses only a completed
`generation-summary.json` for the exact mode/repetition directory. Reviewers
should compare `matrix_manifest.json` before accepting resumed data and must
not merge pilot and formal-3-repetition results. A missing official result is
reported as `not_run`; a generation exit code or non-empty patch is not a
resolved issue.

The deterministic V2.4 demo can be run with:

```bash
python scripts/run_v2_4_demos.py
```

## Sample run artifact list

- `.pico/runs/<run_id>/task_state.json`
- `.pico/runs/<run_id>/trace.jsonl`
- `.pico/runs/<run_id>/report.json`
- `.pico/runs/<run_id>/coding_workflow/<workflow_id>/workflow.json`
