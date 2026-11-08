"""Real process termination + PostgreSQL takeover, using the labeled fixture executor."""

import json
import os
import signal
import subprocess
import sys
import time
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from pico.service.config import Settings
from pico.service.store import LeaseLost, Store


def main():
    settings = Settings.load()
    schema = "demo_recovery_" + uuid4().hex[:12]
    admin = create_engine(settings.database_url)
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    url = make_url(settings.database_url).update_query_dict(
        {"options": "-csearch_path=" + schema}
    )
    store = Store(url)
    store.initialize()
    folder = settings.data_dir.parent / schema
    folder.mkdir(parents=True)
    repository = settings.repositories["demo-recovery"]
    base = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository.path, text=True
    ).strip()
    job = store.submit(
        "recovery-demo",
        {
            "repository_id": "demo-recovery",
            "task": "Fix the intentionally broken addition fixture",
        },
        base_commit=base,
    )[0]
    env = {
        **os.environ,
        "RUNTIME_DATABASE_URL": url.render_as_string(hide_password=False),
        "RUNTIME_DATA_DIR": str(folder),
        "RUNTIME_LEASE_SECONDS": "3",
        "PYTHONUTF8": "1",
    }
    command = [
        sys.executable,
        "-X",
        "utf8",
        "-m",
        "pico.service.worker",
        "--once",
        "--worker-id",
    ]
    first = subprocess.Popen(
        command + ["crash-first"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=(folder / "first.err.log").open("w"),
        text=True,
    )
    actual_pid = json.loads(first.stdout.readline())["pid"]
    deadline = time.monotonic() + 30
    while store.get(job["id"])["state"] != "verifying":
        if first.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError("First worker did not reach the sealed checkpoint")
        time.sleep(0.1)
    print(
        "First attempt sealed a patch; terminating the actual worker process",
        flush=True,
    )
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(actual_pid), "/F"], capture_output=True, check=True
        )
    else:
        os.kill(actual_pid, signal.SIGKILL)
    first.wait(timeout=10)
    time.sleep(3.5)
    second = subprocess.run(
        command + ["takeover-second"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if second.returncode:
        raise RuntimeError("Replacement worker failed; inspect the retained demo logs")
    result = store.get(job["id"])
    stale_rejected = False
    try:
        store.finish(
            job["id"], 1, "crash-first", "succeeded", {"verification_passed": True}
        )
    except LeaseLost:
        stale_rejected = True
    report = {
        "tier": "real_process_fault_demo_with_deterministic_executor_not_model_benchmark",
        "job_id": job["id"],
        "state": result["state"],
        "generation": result["generation"],
        "recovery": (result["result"] or {}).get("recovery"),
        "first_agent_log_present": (folder / job["id"] / "1/agent.log").exists(),
        "second_agent_log_present": (folder / job["id"] / "2/agent.log").exists(),
        "stale_publication_rejected": stale_rejected,
        "attempt_states": [row["state"] for row in store.list_attempts(job["id"])],
        "verification_passed": (result["result"] or {}).get("verification_passed"),
    }
    (folder / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)
    assert result["state"] == "succeeded" and result["generation"] == 2
    assert report["recovery"] == "resume_verification" and stale_rejected
    assert report["first_agent_log_present"] and not report["second_agent_log_present"]
    store.engine.dispose()
    admin.dispose()


if __name__ == "__main__":
    main()
