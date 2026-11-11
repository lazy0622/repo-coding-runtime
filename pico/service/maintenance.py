"""Explicit terminal-job workspace cleanup, retaining accepted patches and traces."""

import hashlib
import shutil
from pathlib import Path

from .store import TERMINAL
from .worker import git


def cleanup_job_workspaces(settings, store, job_id, *, apply=False):
    job = store.get(job_id)
    if job is None or job["state"] not in TERMINAL or not job["checkpoint"]:
        raise ValueError("Only terminal jobs with a sealed patch can be cleaned")
    source_generation = job["checkpoint"]["source_generation"]
    repository = settings.repositories[job["payload"]["repository_id"]]
    results = []
    for attempt in store.list_attempts(job_id):
        # Older unsealed attempts may contain edits absent from the accepted
        # patch. Do not remove that evidence through this command.
        if attempt["generation"] < source_generation or not attempt["workspace"]:
            continue
        folder = settings.data_dir / job_id / str(attempt["generation"])
        workspace = Path(attempt["workspace"])
        expected = folder / "workspace"
        workspace.resolve().relative_to(settings.data_dir.resolve())
        if workspace.resolve() != expected.resolve():
            raise ValueError("Workspace does not match its job and generation")
        if not workspace.exists():
            continue
        records = []
        runs = workspace / ".pico/runs"
        if runs.exists():
            runs.resolve().relative_to(workspace.resolve())
            for source in runs.rglob("*"):
                if source.is_file() and source.suffix in {".json", ".jsonl"}:
                    source.resolve().relative_to(workspace.resolve())
                    target = folder / "runtime-runs" / source.relative_to(runs)
                    target.resolve().relative_to(settings.data_dir.resolve())
                    records.append((source, target))
        if apply:
            for source, target in records:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() and target.read_bytes() != source.read_bytes():
                    raise ValueError(
                        "Existing archive differs; evidence was left intact"
                    )
                shutil.copy2(source, target)
                if (
                    hashlib.sha256(target.read_bytes()).digest()
                    != hashlib.sha256(source.read_bytes()).digest()
                ):
                    raise ValueError("Archive digest mismatch; workspace was retained")
            git(repository.path, "worktree", "remove", "--force", str(workspace))
        results.append(
            {
                "generation": attempt["generation"],
                "archived_records": len(records),
                "removed": apply,
            }
        )
    return {"job_id": job_id, "applied": apply, "workspaces": results}
