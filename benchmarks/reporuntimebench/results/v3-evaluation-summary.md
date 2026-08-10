# Repo Coding Runtime V3 Evaluation Snapshot

Captured on 2026-08-10. Raw local artifacts are generated under `artifacts/`
and intentionally ignored by Git because they include temporary workspace and
trace details.

## Fixed deterministic suite

- 24/24 tasks passed; verifier pass rate 100%; all tasks stayed within budget.
- Patch generation rate for edit-required tasks: 100%.
- Average first edit step: 1.67; read-only tool ratio: 39.0%.
- Includes one post-run hidden contract test, structured blocked state,
  inspection-only tasks, tool/protocol recovery, stage budgets and verification repair.

## Execution-policy ablation

Same 24 fixtures and same scripted model outputs:

| Metric | Policy off | Policy on |
| --- | ---: | ---: |
| Passed | 23/24 | 24/24 |
| Verifier pass rate | 95.8% | 100% |
| Patch generation rate | 95.2% | 100% |
| Average first edit step | 1.70 | 1.67 |
| Read-only tool ratio | 40.0% | 39.0% |

The one-task difference isolates premature-final recovery. This is a harness
regression result, not a model-quality or SWE-bench claim.

## DeepSeek development run

Fixed six-task local development set, temperature 0, one run per condition:

| Metric | Policy off | Policy on |
| --- | ---: | ---: |
| Passed | 6/6 | 6/6 |
| Verifier pass rate | 100% | 100% |
| Average first edit step | 2.83 | 3.17 |
| Read-only tool ratio | 42.3% | 40.0% |

Both conditions passed. The sample is too small and has no repetitions, so it
does not establish a statistically meaningful live-model improvement. An
earlier policy-on attempt exposed an ambiguous fixture contract; the docstring
was corrected before the reported rerun.

## Security quality gate

Three repetitions of fixed local scenarios produced attack block rate 100%,
benign false-block rate 0%, and secret leak rate 0%. These scenarios cover the
implemented local tool and artifact boundaries, not a full penetration test or
network exfiltration analysis.

## External evaluation status

`mini-v1-selection.json` pins ten SWE-bench Lite instances before model runs.
Patch generation and the official predictions adapter are implemented. An
official resolved score is not reported because the local Docker Desktop Linux
engine was not running during this snapshot; only the official Docker harness
may adjudicate resolved tasks.
