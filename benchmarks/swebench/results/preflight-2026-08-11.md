# SWE-bench Lite preflight — 2026-08-11

This report records failures as evidence. It is a pipeline and behavior
preflight, not a statistically meaningful solve-rate measurement.

## Fixed task and budget

- Dataset: `SWE-bench/SWE-bench_Lite`
- Instance: `astropy__astropy-12907`
- Base commit: `d16bfe05a744909de4b27f5875fe0d4ed41ce607`
- Model: `deepseek-v4-pro`
- Agent budget: 24 tool steps, 4096 max new tokens per model response,
  900-second adapter timeout

## Results

| Run | Environment | Agent outcome | Patch | Official result |
| --- | --- | --- | ---: | --- |
| A | Windows generation, Linux Docker grading | completed in 358.015 s; 13 tool steps; first edit at step 13 | 12,206 bytes | unresolved (0/1); patch applied, but 2 FAIL_TO_PASS and 13 PASS_TO_PASS tests failed |
| B | Linux Docker generation with pinned-commit fetch | stopped after 813.204 s; 24 tool steps; no edit | empty | not graded because there was no patch |

## What the preflight proves

- The adapter can pin a real repository commit, generate official prediction
  JSONL, and invoke the official Linux Docker harness.
- Generation success and patch application are insufficient: Run A produced a
  large patch that applied cleanly but regressed existing behavior.
- The current runtime may over-research a large repository and reach the step
  budget without editing, as Run B demonstrates.
- Full repeated evaluation was intentionally stopped after the user disclosed
  a CNY 5 API balance. No repeated-run or solve-rate claim is made.

## Next engineering target

Before spending more API budget, add a repository-size-aware context policy:
prioritize issue-linked symbols and nearby tests, cap repeated index queries,
require a minimal diff, and reserve steps for edit plus verification. Re-run the
same pinned task only after a deterministic regression covers that policy.
