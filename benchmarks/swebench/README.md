# Real-repository benchmark

This adapter evaluates repository work rather than scripted tool-call syntax.
Each manifest row points to a real Git repository and base commit. The adapter
creates a clean checkout, gives the issue to an agent command, captures its Git
diff, and writes the official SWE-bench JSONL predictions format.

`mini-v1-selection.json` pre-registers ten instances before model execution.
`development-v1-selection.json` fixes five of those instances across Astropy
and Django, including the model and execution budget, for three repeated runs.
Keep model, temperature, token/tool budget, timeout and subset IDs fixed. Grade
every `predictions.jsonl` with the official Docker harness; generation success
and non-empty patches are diagnostics, not solve-rate claims.

```powershell
python scripts/prepare_swebench_mini.py `
  --selection benchmarks/swebench/development-v1-selection.json `
  --output artifacts/swebench-development-v1/instances.jsonl

python scripts/run_swebench.py `
  --manifest artifacts/swebench-development-v1/instances.jsonl `
  --output artifacts/swebench-development-v1/runs `
  --repetitions 3 `
  --timeout 900 `
  --model-name deepseek-v4-pro `
  --agent-command-json '["python","-m","pico","--provider","deepseek","--model","deepseek-v4-pro","--approval","auto","--task-mode","edit","--max-steps","24","--max-new-tokens","4096","{problem_statement}"]'
```

The manifest accepts official fields (`instance_id`, `repo`, `base_commit`,
`problem_statement`) and an optional local `repo_path` for offline smoke tests.

`mini-v1-selection.json` pins the first ten SWE-bench Lite test rows before any
model run. Resolve their repository commits and problem statements with
`python scripts/prepare_swebench_mini.py`. This prevents selecting only tasks
that happened to succeed. Official grading requires a running Linux Docker
engine; patch generation on Windows is not an official resolved score.

Report both successes and failures. The experiment summary records agent
completion rate, non-empty patch rate, average tool steps and average first-edit
step. Only the official harness report may be used for test-pass/solve-rate
claims.

For reproducible Linux patch generation, build the small Git + Python image once:

```powershell
docker build -t repo-runtime-swebench-generator `
  -f benchmarks/swebench/Dockerfile.generator .
```

Mount the source read-only, mount `artifacts/` read-write, and pass provider
secrets with `--env-file .env`. The adapter initializes one bare cache per
repository and shallow-fetches each pinned `base_commit`; it does not mirror all
remote refs.

## V4 fixed generation matrix

`scripts/run_swebench_matrix.py` adds a reproducible matrix around the
adapter. It requires the pre-registered selection plus its resolved manifest,
keeps the model/budget/sandbox/instance IDs fixed, and can compare
`ExecutionPolicy` with `--mode both`:

```powershell
python scripts/run_swebench_matrix.py `
  --selection benchmarks/swebench/development-v1-selection.json `
  --manifest artifacts/swebench-development-v1/instances.jsonl `
  --mode both `
  --repetitions 1 `
  --generate-only `
  --agent-command-json '["python","-m","pico","--provider","deepseek","--approval","auto","--task-mode","edit","--max-new-tokens","4096","{problem_statement}"]' `
  --output-dir artifacts/swebench/results/v4-pilot
```

Use `--repetitions 3` for the separate formal run; do not pool it with the
pilot. The matrix writes `generation_metrics`, per-instance process metrics,
`policy_ablation.json/.md`, and a `failures/` directory. `--resume` reuses
completed mode/repetition summaries.

Official grading is intentionally a second step. After the official Docker
harness produces its result artifact, parse it with `--grade-only
--official-results <path>`. The parser is the only source of
`official_resolved` and `official_resolved_rate`; an agent exit code, a
non-empty patch, or a local verifier pass is never presented as solve rate.
See [`docs/evaluation/swebench-methodology.md`](../../docs/evaluation/swebench-methodology.md)
for the full pilot/formal protocol.
