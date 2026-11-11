import json

import pytest
from sqlalchemy import text

from pico.service.maintenance import cleanup_job_workspaces
from pico.service.worker import Worker


def test_cleanup_preserves_runtime_evidence_and_accepted_patch(store, configured):
    settings, job, _ = configured
    Worker(settings, store).run_one()
    completed = store.get(job["id"])
    patch = settings.data_dir / completed["checkpoint"]["patch_path"]
    before = patch.read_bytes()
    workspace = settings.data_dir / job["id"] / "1/workspace"
    report = workspace / ".pico/runs/evidence/report.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({"status": "completed"}))
    preview = cleanup_job_workspaces(settings, store, job["id"])
    assert preview["workspaces"][0]["removed"] is False and workspace.is_dir()
    cleanup_job_workspaces(settings, store, job["id"], apply=True)
    assert not workspace.exists()
    assert (
        workspace.parent / "runtime-runs/evidence/report.json"
    ).read_bytes() == b'{"status": "completed"}'
    assert patch.read_bytes() == before
    assert store.get(job["id"])["state"] == "succeeded"


def test_active_unsealed_job_is_not_cleaned(store, configured):
    settings, job, _ = configured
    with pytest.raises(ValueError, match="terminal jobs"):
        cleanup_job_workspaces(settings, store, job["id"], apply=True)


def test_cleanup_rejects_path_outside_task_storage(store, configured):
    settings, job, _ = configured
    Worker(settings, store).run_one()
    source = settings.repositories["demo"].path
    with store.engine.begin() as connection:
        connection.execute(
            text("UPDATE runtime_attempts SET workspace=:p WHERE job_id=:j"),
            {"p": str(source), "j": job["id"]},
        )
    with pytest.raises(ValueError):
        cleanup_job_workspaces(settings, store, job["id"], apply=True)
    assert source.is_dir() and (source / "calculator.py").is_file()
