# Real-repository benchmark

This adapter evaluates repository work rather than scripted tool-call syntax.
Each manifest row points to a real Git repository and base commit. The adapter
creates a clean checkout, gives the issue to an agent command, captures its Git
diff, and writes the official SWE-bench JSONL predictions format.

Start with a pinned 10-instance smoke subset, then 20 development instances and
50 final instances from SWE-bench Lite or Verified. Keep model, temperature,
token/tool budget, timeout and subset IDs fixed. Grade `predictions.json` with
the official Docker harness; generation success and non-empty patches are
diagnostics, not solve-rate claims.

```powershell
python scripts/run_swebench.py `
  --manifest benchmarks/swebench/instances.jsonl `
  --output artifacts/swebench-smoke `
  --agent-command-json '["repo","--approval","auto","{problem_statement}"]'
```

The manifest accepts official fields (`instance_id`, `repo`, `base_commit`,
`problem_statement`) and an optional local `repo_path` for offline smoke tests.

`mini-v1-selection.json` pins the first ten SWE-bench Lite test rows before any
model run. Resolve their repository commits and problem statements with
`python scripts/prepare_swebench_mini.py`. This prevents selecting only tasks
that happened to succeed. Official grading requires a running Linux Docker
engine; patch generation on Windows is not an official resolved score.
