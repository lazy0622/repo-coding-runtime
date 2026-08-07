import shutil
import subprocess

import pytest

from pico.workspace_isolation import WorkspaceIsolationError, WorkspaceLease


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
def test_workspace_lease_preserves_dirty_worktree_until_explicit_discard(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init")
    git(source, "config", "user.email", "pico@example.test")
    git(source, "config", "user.name", "Pico Test")
    (source / "README.md").write_text("demo\n", encoding="utf-8")
    git(source, "add", "README.md")
    git(source, "commit", "-m", "initial")

    lease = WorkspaceLease.create(source, base_dir=tmp_path / "leases")
    (lease.workspace_root / "README.md").write_text("changed\n", encoding="utf-8")

    with pytest.raises(WorkspaceIsolationError, match="uncommitted changes"):
        lease.remove()

    assert lease.workspace_root.exists()
    lease.remove(discard=True)
    assert not lease.workspace_root.exists()
    assert lease.created is False
