"""Opt-in Git worktree isolation for agent runs.

Creation is automatic only when explicitly requested. Cleanup is conservative:
a dirty task worktree is never removed unless the caller passes ``discard=True``.
"""

from __future__ import annotations

import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path


class WorkspaceIsolationError(RuntimeError):
    pass


def _git(cwd, *args, check=True):
    result = subprocess.run(
        ["git", *args],
        cwd=Path(cwd),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise WorkspaceIsolationError(detail)
    return result


@dataclass
class WorkspaceLease:
    source_root: Path
    workspace_root: Path
    lease_id: str
    created: bool = True

    @classmethod
    def create(cls, source, base_dir=None):
        source = Path(source).resolve()
        result = _git(source, "rev-parse", "--show-toplevel")
        source_root = Path(result.stdout.strip()).resolve()
        if base_dir is None:
            base_dir = Path(tempfile.gettempdir()) / "pico-worktrees"
        base_dir = Path(base_dir).resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        lease_id = "pico-" + uuid.uuid4().hex[:10]
        target = base_dir / lease_id
        _git(source_root, "worktree", "add", "--detach", str(target), "HEAD")
        return cls(source_root=source_root, workspace_root=target, lease_id=lease_id)

    def status(self):
        if not self.workspace_root.exists():
            return "missing"
        return _git(self.workspace_root, "status", "--short", check=False).stdout.strip() or "clean"

    def remove(self, discard=False):
        status = self.status()
        if status not in {"clean", "missing"} and not discard:
            raise WorkspaceIsolationError("task worktree has uncommitted changes; pass discard=True to remove it")
        if status == "missing":
            self.created = False
            return
        args = ["worktree", "remove"]
        if discard:
            args.append("--force")
        args.append(str(self.workspace_root))
        _git(self.source_root, *args)
        self.created = False

    def to_dict(self):
        return {
            "lease_id": self.lease_id,
            "source_root": str(self.source_root),
            "workspace_root": str(self.workspace_root),
            "created": bool(self.created),
            "status": self.status(),
        }
