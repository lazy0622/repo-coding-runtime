"""Submit the frozen five-task set through the real authenticated service API."""

import argparse
import hashlib
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from pico.service.config import Settings
from pico.service.store import Store

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / "artifacts/service/real-tasks-v1"


def main(label=""):
    if label and not re.fullmatch(r"[a-z0-9-]{1,40}", label):
        raise ValueError(
            "Use 1-40 lowercase letters, digits or hyphens for the run label"
        )
    settings = Settings.load()
    run_folder = FOLDER if not label else FOLDER.parent / ("real-regression-" + label)
    public_folder = (
        Path(__file__).parent if not label else Path(__file__).parent / "reruns" / label
    )
    run_folder.mkdir(parents=True, exist_ok=True)
    public_folder.mkdir(parents=True, exist_ok=True)
    store = Store(settings.database_url)
    frozen_bytes = (FOLDER / "frozen.json").read_bytes()
    frozen = json.loads(frozen_bytes)
    assert (
        frozen["cases_sha256"]
        == hashlib.sha256(
            Path(__file__).with_name("cases.json").read_bytes()
        ).hexdigest()
    )
    assert (
        frozen["verifier_sha256"]
        == hashlib.sha256(
            Path(__file__).with_name("verify.py").read_bytes()
        ).hexdigest()
    )
    digest = hashlib.sha256(frozen_bytes).hexdigest()

    def request(path, data=None, key=None):
        headers = {"Authorization": "Bearer " + settings.api_token}
        if key:
            headers["Idempotency-Key"] = key
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            "http://127.0.0.1:8010" + path,
            data=None if data is None else json.dumps(data).encode(),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)

    submitted = {}
    for case in frozen["cases"]:
        response = request(
            "/api/jobs",
            {"repository_id": case["id"], "task": case["task"]},
            "real-v1-"
            + (label + "-" if label else "")
            + digest[:16]
            + "-"
            + case["id"],
        )
        submitted[case["id"]] = response["job"]["id"]
    (run_folder / "submitted.json").write_text(json.dumps(submitted, indent=2))
    print("Submitted frozen five-case set through /api/jobs", flush=True)
    deadline = time.monotonic() + 900
    pending = dict(submitted)
    while pending and time.monotonic() < deadline:
        for case_id, job_id in list(pending.items()):
            job = request("/api/jobs/" + job_id)
            if job["state"] in {"succeeded", "failed", "cancelled"}:
                print(json.dumps({"case": case_id, "state": job["state"]}), flush=True)
                del pending[case_id]
        if pending:
            time.sleep(2)

    results = []
    patch_folder = public_folder / "patches"
    patch_folder.mkdir(exist_ok=True)
    for case in frozen["cases"]:
        job_id = submitted[case["id"]]
        job = store.get(job_id)
        attempts = store.list_attempts(job_id)
        rows = []
        for attempt in attempts:
            folder = settings.data_dir / job_id / str(attempt["generation"])
            counts = {"input_tokens": 0, "output_tokens": 0}
            calls = 0
            traces = list(folder.glob("workspace/.pico/runs/*/trace.jsonl")) or list(
                folder.glob("runtime-runs/*/trace.jsonl")
            )
            for trace in traces:
                for line in trace.read_text(encoding="utf-8").splitlines():
                    event = json.loads(line)
                    if event.get("event") == "model_parsed":
                        metadata = event.get("completion_metadata", {})
                        for name in counts:
                            counts[name] += metadata.get(name, 0) or 0
                        calls += 1
            runtime_reports = list(
                folder.glob("workspace/.pico/runs/*/report.json")
            ) or list(folder.glob("runtime-runs/*/report.json"))
            runtime = (
                json.loads(runtime_reports[-1].read_text(encoding="utf-8"))
                if runtime_reports
                else {}
            )
            rows.append(
                {
                    "generation": attempt["generation"],
                    "state": attempt["state"],
                    "elapsed_seconds": round(
                        (
                            attempt["finished_at"] - attempt["started_at"]
                        ).total_seconds(),
                        2,
                    )
                    if attempt["finished_at"]
                    else None,
                    "model_calls": calls,
                    "observed_usage": counts if calls else None,
                    "runtime_status": runtime.get("status"),
                    "runtime_stop_reason": runtime.get("stop_reason"),
                    "tool_steps": runtime.get("tool_steps"),
                }
            )
        if job["checkpoint"]:
            data = (settings.data_dir / job["checkpoint"]["patch_path"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == job["checkpoint"]["sha256"]
            (patch_folder / (case["id"] + ".patch")).write_bytes(data)
        results.append(
            {
                "case_id": case["id"],
                "source_url": case["source_url"],
                "upstream_base_commit": case["upstream_base_commit"],
                "fix_reference_commit": case["fix_commit"],
                "base_snapshot_commit": case["base_snapshot_commit"],
                "preflight_exit_codes": case["preflight_exit_codes"],
                "base_upstream_regression_exit_code": case[
                    "base_upstream_regression_exit_code"
                ],
                "state": job["state"],
                "result": job["result"],
                "attempts": rows,
            }
        )
    report = {
        "tier": "known_task_regression_not_new_heldout" if label else frozen["tier"],
        "run_label": label or "baseline",
        "service_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT
        )
        .decode()
        .strip(),
        "frozen_manifest_sha256": digest,
        "model": "deepseek-flash",
        "thinking": False,
        "max_model_steps_per_attempt": 12,
        "task_timeout_seconds": settings.task_timeout,
        "verification_timeout_seconds": settings.verify_timeout,
        "cost_currency_amount": None,
        "cost_note": "Provider token usage recorded; monetary cost not returned by provider.",
        "total": len(results),
        "succeeded": sum(item["state"] == "succeeded" for item in results),
        "agent_naturally_completed": sum(
            bool(item["attempts"])
            and item["attempts"][-1]["runtime_status"] == "completed"
            for item in results
        ),
        "results": results,
    }
    serialized = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    for target in (run_folder / "results.json", public_folder / "RESULTS.json"):
        target.write_text(serialized, encoding="utf-8")
    print(
        json.dumps({"total": report["total"], "succeeded": report["succeeded"]}),
        flush=True,
    )
    if pending:
        raise SystemExit("Budget elapsed; unfinished tasks retained in results")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-label", default="")
    main(parser.parse_args().run_label)
