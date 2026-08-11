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
