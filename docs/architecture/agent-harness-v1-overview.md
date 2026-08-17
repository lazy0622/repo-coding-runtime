# Repo Coding Runtime Architecture

Repo Coding Runtime turns a model-plus-tools loop into a small but complete Coding Agent Runtime. The model proposes actions; the runtime owns state, policy, execution, context, recovery, isolation, and evidence. The source package remains `pico` for compatibility.

## Core Boundary

```text
Model output
    ↓
AgentLoop ──→ TaskState / RunEvent / Checkpoint
    ↓
ToolGateway (validation, allowlist, approval, snapshots, error mapping)
    ↓
ToolRegistry
    ├── built-in repository tools
    └── namespaced MCP tools
```

There is one loop, one session model, one registry, and one execution gateway. Skills influence prompt context but never bypass the gateway.

The default V1 tool surface intentionally excludes the experimental read-only
`delegate` capability. V2 adds a separate opt-in Supervisor with validated task
graphs, bounded child runtimes, lifecycle events, and optional worktree
isolation; V1 remains single-agent by default.

## Runtime Flow

1. Build workspace context, discover Skills, register built-in and MCP tools, and compute runtime identity.
2. Record the request and create a task in the `created` phase.
3. Transition to `planning`; build bounded context with relevant memory and lazily selected Skills.
4. Parse model output into a tool call, retry notice, or final answer.
5. Transition to `executing`; send every tool through Tool Gateway.
6. For risky interactive actions, transition through `waiting_approval`.
7. Record policy and tool lifecycle events; capture workspace snapshots and a checkpoint for risky or workspace-changing work; update memory.
8. Finish in `completed`, `stopped`, or `failed`, then atomically persist the final report.

## Runtime State Contract

Legal phases are `created`, `planning`, `executing`, `verifying`, `waiting_approval`, `paused`, `completed`, `stopped`, and `failed`. `phase_history` stores timestamped transitions and reasons. Old snapshots without a phase derive a terminal phase from their existing status.

Status describes the outcome; phase describes where execution currently is. Stop reason remains a separate field so evaluation can distinguish a successful final answer, step limit, retry limit, and provider failure.

## Tool Contract

`ToolRegistry` stores stable `ToolSpec` metadata: schema, source, risk, runner, JSON input schema, and provider metadata. The prompt/checkpoint signature excludes callables and hashes this stable contract.

`ToolGateway` is the only execution path. It applies:

- allowlist and existence checks;
- built-in or JSON-Schema argument validation;
- duplicate-call protection;
- approval policy and read-only restrictions;
- before/after workspace snapshots for risky tools;
- normalized success, rejection, failure, and partial-success metadata;
- trace redaction through the runtime event boundary.

The older `ToolExecutor` name remains a compatibility alias. The gateway emits one terminal `tool_finished` event per call; the metrics layer also accepts historical `tool_completed` and `tool_executed` artifacts.

## Skills

Skills are bounded `SKILL.md` files with frontmatter and instructions. Discovery is deterministic, malformed files become diagnostics, duplicate names are rejected, oversized or symlinked files are not loaded, and content participates in runtime identity.

Selection is lazy: at most three relevant Skills whose declared tools are available are appended to the bounded prompt context. V1 never executes code from a Skill directory.

## MCP

An MCP stdio client handles initialize, paginated tool discovery, and tool calls. `MCPToolProvider` adapts discovered tools into namespaced `ToolSpec` entries. MCP calls therefore inherit Repo Coding Runtime's normal validation, risk, approval, trace, and error behavior.

The client process receives a restricted environment plus explicitly configured variables. Provider discovery failures are reported without taking down Repo Coding Runtime unless strict mode is requested.

## Workspace Isolation

Direct execution remains the default. `--workspace-mode worktree` creates an opt-in detached Git worktree. Cleanup is conservative: a dirty worktree cannot be removed without an explicit discard decision. This keeps isolation useful without silently deleting agent output.

## State Artifacts

- `task_state.json` records phase history, attempts, tool steps, status, stop reason, and final answer.
- `trace.jsonl` uses schema-versioned events with stable run/task/status/phase identity while retaining the legacy event name.
- `report.json` records prompt metadata, durable memory changes, loaded Skills, provider diagnostics, tool providers, and workspace-isolation evidence.

Session, task state, and report JSON use write-then-replace persistence. Trace remains append-only JSONL so an interrupted run still leaves a useful event timeline.

Durable memory promotion is opt-in in V1. Working memory and file summaries are
updated during normal execution; heuristic promotion from a final answer only
runs when the caller explicitly enables it.

## V1.5 Plan–Execute–Verify

V1.5 adds a serial `PlanState` with resumable `PlanTask` nodes. A model may
return a JSON `<plan>` envelope; the runtime persists the plan in the session,
checkpoint, task state, and final report. The default fallback is one task for
the user request, so existing V1 model output remains valid.

When `verify_command` is configured, a final answer enters the `verifying`
phase before the run is accepted. The verifier runs in the workspace with the
restricted shell environment, emits `verification_started` and
`verification_finished`, and returns to planning with bounded retry evidence
when the command fails. Destructive shell patterns are blocked before
execution, while high-risk but non-blocked commands retain their approval
boundary.

`pico --replay` reads append-only `trace.jsonl` and renders a compact timeline
of state transitions, tool calls, plan updates, and verification results. It
is intentionally a read-only forensic view rather than another execution
path.

## V2 Supervisor

V2 exposes `run_task_graph` only when `--v2`/`--enable-subagents` is selected.
The Supervisor validates a bounded DAG, schedules ready tasks deterministically,
injects completed dependency evidence into each child prompt, and records child
sessions under the parent run's `subagents/` directory. Children run with
`read_only=True` and an allowlist containing repository navigation, Repo Index,
and diff-preview tools only. A failed child marks dependent tasks blocked.

The V2.0 scheduler is intentionally serial for reproducible provider usage and
artifacts. It is a task-graph/lifecycle foundation, not a distributed worker
queue or an automatic code-merge system.

## V2.1–V2.4 Evidence and Coding Workflow

Completed child tasks are normalized into `EvidenceBundle` records rather than
being passed as unbounded prose. Each bundle carries findings, risks,
recommendations, confidence, and file/line/symbol evidence. The Supervisor
deduplicates those records into a summary and serializes completed dependency
evidence into the next child prompt.

`GraphTask` persists attempt count, timeout budget, retry history, and evidence
in `task_graph.json`. A resumed graph requeues interrupted `running` tasks
when they still have budget; exhausted tasks fail and their dependents become
blocked. This makes recovery explicit instead of silently replaying the whole
graph.

`run_coding_workflow` adds a guarded delivery path. The caller supplies the
research DAG, a strict unified diff, and an explicit verifier. The runtime
records research, previews and applies the diff through `PatchJournal`, runs
the verifier, and rolls back only when the backup fingerprint still matches.
The parent/model is still responsible for proposing the patch; this tool
enforces the order and safety boundary rather than claiming autonomous code
generation.

## V4 Code Intelligence and Execution Backends

RepoIndex v3 extends navigation with Python `CallRecord` evidence and bounded
`analyze_impact`. It reports definitions, direct/indirect callers, callees,
related tests, diagnostics and confidence. The index is a persistent
navigation cache, not a substitute for reading source or running tests; the
parser stays conservative when a dynamic call cannot be resolved.

Shell and verifier execution use an explicit backend boundary. Host mode keeps
local development compatible. Docker mode creates a persistent session with a
fixed workspace mount, no network by default, a non-root user, read-only root
filesystem, resource limits, environment allowlisting, timeout/OOM reporting
and cleanup. Selecting Docker never silently falls back to Host. Worktree
isolation protects Git changes; the Docker backend limits the process
environment. They solve different failure classes and are intentionally
composable.

## V4 Evaluation Boundary

The fixed real-repository evaluation is split into generation and official
grading. The matrix runner fixes the selection, commit, model budget,
temperature, timeout and sandbox, then compares `ExecutionPolicy` on/off while
retaining failures. `generation_metrics` contains process and patch evidence;
only an official Docker harness artifact can populate `official_resolved`.
Deterministic RepoRuntimeBench remains the fast regression suite for runtime
contracts, not a live-model or SWE-bench solve-rate measurement.

## V4.4 Native Tool Protocol

The provider adapter converts the active `ToolRegistry` into provider-specific
schemas. OpenAI Responses uses function tools with `parameters`; Anthropic
Messages uses `input_schema` and returns `tool_use` blocks. Both are normalized
into the runtime's `ToolCall` object before the existing ToolGateway boundary.

Text-only providers keep the XML protocol as a compatibility fallback. This is
deliberate: native calling improves argument transport and provider fidelity,
but it must not bypass approval, argument validation, ExecutionPolicy, patch
journaling, or sandbox selection. The current loop accepts one native call per
model turn, records its protocol/call id in Trace and Session history, and
continues with the existing multi-turn tool-result flow.
