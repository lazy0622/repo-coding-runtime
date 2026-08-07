# Repo Coding Runtime V1.5.1 Demos

Run from the repository root:

```bash
python scripts/run_benchmark.py
python scripts/run_v1_5_1_demos.py
```

The benchmark uses deterministic `FakeModelClient` outputs and fresh fixture
copies. It covers:

- Plan–Execute–Verify success;
- Verification failure followed by a bounded repair;
- Plan and Trace evidence suitable for replay.

The demo runner produces three workspaces under `artifacts/v1_5_1_demos/`:

- `plan-verify/`: a plan, patch, external verification and final report;
- `safety-boundary/`: a destructive command blocked before shell execution;
- `replay-evidence/`: a plan, read-only tool call and `replay.txt` timeline.

Each case keeps `task_state.json`, `trace.jsonl`, `report.json` and a rendered
replay view. The output directory is ignored by Git because it contains local
run artifacts.
