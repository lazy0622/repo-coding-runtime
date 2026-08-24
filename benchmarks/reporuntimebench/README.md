# RepoRuntimeBench v1

RepoRuntimeBench is the local, reproducible benchmark for Repo Coding Runtime.
It complements SWE-bench: SWE-bench measures autonomous issue resolution on
large external repositories, while this suite isolates runtime behavior that a
local repository agent must provide reliably.

The fixed set contains 24 deterministic tasks covering single-file and
cross-file fixes, inspection-only work, structured blockers, protocol/tool
recovery, stage budgets, premature-final recovery, repeated-read suppression,
and verification-driven repair. Every task runs in a fresh fixture copy. One
contract test is injected only after the agent exits, so the agent cannot read
the hidden assertions before producing its patch.

Run it with:

```powershell
.\.venv\Scripts\python.exe scripts\run_reporuntime_benchmark.py
```

The JSON and Markdown reports are written under
`artifacts/reporuntimebench-v1/`. Deterministic results prove harness contracts,
not live-model quality. Provider comparisons must use the same manifest,
model settings, step budget, and repeated-run policy, and must publish failures
alongside successes.

Run the policy on/off ablation with identical fixtures and model outputs:

```powershell
python scripts/run_policy_ablation.py
```

After configuring DeepSeek in `.env`, run either the one-task smoke or the
fixed six-task development set:

```powershell
.\.venv\Scripts\python.exe scripts\run_reporuntime_live_smoke.py
.\.venv\Scripts\python.exe scripts\run_reporuntime_live_smoke.py --suite development --policy on
```

Its report is written under `artifacts/reporuntimebench-live/` and must be
described as a smoke result, not as a benchmark solve rate.

## V4 release evidence

The versioned V4 evidence summary combines deterministic harness results,
ExecutionPolicy ablation, security quality metrics, fixed SWE-bench selection
hashes, Git provenance and the official-grade boundary. It is generated from
local/CI artifacts without calling a model:

```powershell
python scripts/write_v4_evidence.py --verification-status passed
```

The committed summary is
`results/v4-evidence-summary.json` with a Markdown companion. A local working
tree is explicitly marked dirty; after a release commit, regenerate the
summary so its `git_revision` identifies that commit. Missing live-model or
official Docker artifacts remain `not_run` rather than being inferred from a
non-empty patch.

## Native Tool Protocol regression

The provider-native tool-call adapter has a deterministic, network-free
contract benchmark. It checks native call normalization, malformed and
multiple-call rejection, XML fallback, and OpenAI/Anthropic schema conversion:

```powershell
python scripts/run_tool_protocol_benchmark.py
```

The JSON and Markdown artifacts are written to
`artifacts/tool-protocol-v1/`. This benchmark validates the runtime protocol
and provider adapters; it does not measure model quality or live API success.
