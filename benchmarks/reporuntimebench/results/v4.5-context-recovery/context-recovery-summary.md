# Repo Coding Runtime P0/P1 Context–Memory–Recovery Benchmark

- Captured (UTC): `2026-08-18T03:39:39.702003+00:00`
- Git revision: `d80cd3862e661a51b07d658b937bcaac7d4a5e72`
- Model client: `FakeModelClient/scripted deterministic clients`
- External API calls: `0`; network required: `false`

## P0 Context and Memory

| Metric | Result | Sample/definition |
| --- | ---: | --- |
| Context configurations | 12 | 24 deterministic runs |
| Average prompt chars | 9634.33 → 6173.67 | no reduction → full context governance |
| Average compression | 34.64% | across fixed context matrix |
| Maximum compression | 43.10% | worst/best fixed configuration |
| Current request preserved | 100.00% | current request suffix check |
| Memory tasks | 12 | 24 runs per variant |
| Follow-up repeated reads | 24 → 0 | memory off → memory on |
| Repeated-read reduction | 100.00% | same scripted task matrix |
| Average tool steps | 1.00 → 0.00 | memory off → memory on |
| Memory-on correctness | 100.00% | verifier is scripted fact equality |

## P1 Checkpoint / Resume / Workspace Drift

| Metric | Result | Sample/definition |
| --- | ---: | --- |
| Recovery scenarios | 10 | 20 runs per variant |
| Valid resume success | 100.00% | 8 valid-state runs |
| Resume status accuracy | 100.00% | expected status per fixed scenario |
| Stale re-anchor | 100.00% | partial-stale cases |
| Workspace drift detection | 100.00% | workspace-mismatch cases |
| Invalid-state rejection | 100.00% | 12 stale/mismatch/schema runs |
| Stale-state false accept | 0.00% | invalid state accepted as full-valid |

## Interpretation boundary

- P0 measures context/memory module behavior under fixed deterministic fixtures; it is not a live-model token or quality benchmark.
- P1 measures whether checkpoint state is accepted, re-anchored, or rejected according to the injected workspace condition.
- Keep these metrics separate from real-provider development results and official SWE-bench grading.
