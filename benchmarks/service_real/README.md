# Five real repository tasks through the task service

Run date: 2026-11-08. Runtime/service revision before evaluation: `90541ef`.

This is a curated historical validation set, not SWE-bench or an unseen generalization benchmark. We selected three humanize fixes and two itsdangerous fixes from their public Git history. Sources and reference commits are in `cases.json`. The evaluator author inspected historical fixes to curate tasks; the model received only the problem descriptions and base snapshots, not reference patches or future Git history. Training-data contamination is not excluded. Host execution does not enforce a security boundary hiding other host files.

## Preparation and checks

- Base is each upstream fix's first parent. No artificial bug was injected.
- Snapshot Git repositories contain only that source tree, not the future solution history. Humanize snapshots additionally track a generated `_version.py` metadata stub (`0+evaluation`) needed for importability; it does not change runtime behavior under test.
- The independent behavioral checks fail on all five bases and pass on all five reference fixes.
- All selected existing regression files also pass on the bases: 232 / 227 / 70 / 112 / 17 tests respectively.
- The trusted verifier lives outside the Agent worktree and checks those regression files remain unchanged. Source paths are forced to the candidate workspace before imports. Candidate patches are applied in a fresh verification worktree by the service.
- Cases and verifier were SHA-256 frozen before the first model run. No task-specific Runtime tuning or retries were performed after seeing outcomes.

## Results

| Case | Independently verified patch | Agent stop reason | Attempt seconds | Observed model calls |
|---|---|---|---:|---:|
| humanize: large native integers | pass | step_limit_reached | 30.63 | 13 |
| humanize: fraction reduces to whole | pass | step_limit_reached | 33.77 | 13 |
| humanize: rounded size unit carry | pass | final_answer_returned | 26.09 | 11 |
| itsdangerous: future timestamps | pass | step_limit_reached | 27.31 | 13 |
| itsdangerous: None salt compatibility | pass | step_limit_reached | 27.19 | 13 |

**Patch-and-regression acceptance: 5/5. Agent natural completion: 1/5.** Four runs stopped at the Runtime step limit after producing a valid patch; the service then sealed it and its independent verifier accepted it. These are different metrics. `--max-steps 12` is the configured tool-step budget, not a promise that the number of model requests never exceeds twelve. `RESULTS.json` preserves both service and Runtime outcomes and observed usage; accepted diffs are in `patches/`.

Model: `deepseek-flash`, existing Anthropic-compatible adapter, thinking disabled. Each attempt has 240 seconds and verification has 60 seconds, with two local workers. Usage is summed from recorded completion metadata; no currency cost is supplied by the provider. Queue time is not included in the attempt duration. No confidence interval or general success-rate claim is justified by five curated cases.

The clearest next improvement is efficient termination / tool protocol handling: four accepted patches still exhausted the tool budget. Preserve this baseline before changing that policy. Do not silently rerun or replace failed outcomes.

## Reproduce

Install `requirements.txt` in the service environment. Clone the two upstream source histories to `artifacts/service/source-cache/humanize` and `artifacts/service/source-cache/itsdangerous`. From the Runtime repository root:

```powershell
python benchmarks/service_real/prepare.py
# The preparation script extends the local trusted repositories.json.
# Restart the API and idle workers so the new registry is loaded.
python benchmarks/service_real/run.py
```

The service, PostgreSQL and local credentials must already be configured; see `SERVICE_UPGRADE.md`. Preparation refuses to overwrite a frozen run. Submission uses stable per-case idempotency keys; collecting the same run again does not create new model tasks. Local full traces and snapshot repositories are ignored by Git; the public results are whitelisted summaries without credentials or absolute execution paths.

Sources: [humanize #392](https://github.com/python-humanize/humanize/pull/392), [#354](https://github.com/python-humanize/humanize/pull/354), [#329](https://github.com/python-humanize/humanize/pull/329), [itsdangerous future timestamp fix](https://github.com/pallets/itsdangerous/commit/c30678d19e37011890e2374cca04f7789e101793), [salt compatibility fix](https://github.com/pallets/itsdangerous/commit/41ec419632aae3533ff12838044eeddd9cd1311f). The original libraries retain their MIT/BSD licensing and authorship; the generated repair diffs are evaluation artifacts, not claims of upstream authorship.
