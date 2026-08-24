# Fixed SWE-bench development evaluation

This project separates three different claims:

1. **Generation metrics**: whether the agent process completed, produced a
   non-empty patch, and how it reached that patch.
2. **Official grade**: whether the official SWE-bench Docker harness accepted
   the patch for the pinned instance.
3. **Policy ablation**: a controlled comparison of `ExecutionPolicy` on and
   off under the same task list and budget.

`agent_completed`, `non_empty_patch_rate` and `final_verifier_pass_rate` are
generation diagnostics. They must never be reported as SWE-bench solve rate.
Only `pico/evaluation/swebench_results.py` can populate
`official_resolved`/`official_resolved_rate`, and only when an official grader
result artifact is supplied.

## Fixed selection and manifest

`benchmarks/swebench/development-v1-selection.json` is the pre-registered
selection. It fixes the five instance IDs, model budget and selection policy
before any generation run. Resolve it to a JSONL manifest once:

```powershell
python scripts/prepare_swebench_mini.py `
  --selection benchmarks/swebench/development-v1-selection.json `
  --output artifacts/swebench/development-v1/instances.jsonl
```

The resolved manifest pins `instance_id`, repository, `base_commit` and
`problem_statement`. Do not replace IDs after seeing agent outcomes.

## Pilot generation

Use one repetition first. The matrix runner executes the same fixed command
under both policy modes and writes all failures:

```powershell
python scripts/run_swebench_matrix.py `
  --selection benchmarks/swebench/development-v1-selection.json `
  --manifest artifacts/swebench/development-v1/instances.jsonl `
  --mode both `
  --repetitions 1 `
  --generate-only `
  --model deepseek-v4-pro `
  --temperature 0.2 `
  --max-agent-steps 24 `
  --timeout 900 `
  --sandbox-mode host `
  --agent-command-json '["python","-m","pico","--provider","deepseek","--approval","auto","--task-mode","edit","--max-new-tokens","4096","{problem_statement}"]' `
  --output-dir artifacts/swebench/results/v4-pilot
```

This is ten generation calls for five instances (`policy_on` and
`policy_off`). The runner does not call an official grader and the command
above does not claim a solve rate. `--resume` reuses a completed
`generation-summary.json` for a mode/repetition pair.

## Formal fixed run

Only after the pilot environment is stable, run the pre-registered three
repetitions into a different directory. Do not combine pilot and formal
results:

```powershell
python scripts/run_swebench_matrix.py `
  --selection benchmarks/swebench/development-v1-selection.json `
  --manifest artifacts/swebench/development-v1/instances.jsonl `
  --mode both `
  --repetitions 3 `
  --model deepseek-v4-pro `
  --temperature 0.2 `
  --max-agent-steps 24 `
  --timeout 900 `
  --sandbox-mode host `
  --agent-command-json '["python","-m","pico","--provider","deepseek","--approval","auto","--task-mode","edit","--max-new-tokens","4096","{problem_statement}"]' `
  --output-dir artifacts/swebench/results/v4-formal-3rep
```

The output contains:

```text
matrix_manifest.json       # selection, budget, policy, Git provenance
generation_summary.json    # generation_metrics and per-instance rows
policy_ablation.json/.md  # policy_on vs policy_off diagnostics
failures/                  # preserved bad cases, including empty patches
```

Each run records tool steps, first edit step, discovery/verification steps,
verification repairs, repeated rejections, verifier status, observed token
usage, duration, patch bytes and sandbox mode. Token fields remain `null` when
the provider does not expose usage; the runner does not estimate tokens.

## Official grading

Run the official Linux/Docker SWE-bench harness separately, using each
generated `predictions.jsonl`. Then parse the harness result without
re-running the agent:

```powershell
python scripts/run_swebench_matrix.py `
  --grade-only `
  --selection benchmarks/swebench/development-v1-selection.json `
  --manifest artifacts/swebench/development-v1/instances.jsonl `
  --official-results <official-harness-result-file-or-directory> `
  --output-dir artifacts/swebench/results/v4-formal-3rep-grade
```

`official_grade_summary.json` reports `graded`, `partial`, `not_run` or
`failed`. A missing or incomplete official artifact does not become zero
successes by accident, and a generation success does not become
`official_resolved`.

## Reproducibility boundary

The matrix manifest records the selection, budget, sandbox mode, current Git
revision and working-tree diff digest. Public reports should normalize local
absolute paths and retain both successful and failed instances. A fixed
development set is evidence for this runtime's engineering process; it is not
an overall SWE-bench Lite leaderboard result.
