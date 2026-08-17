"""Narrow context passed from runtime into tool functions."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class ToolContext:
    root: Path
    path_resolver: Callable[[str], Path]
    shell_env_provider: Callable[[], dict]
    depth: int
    max_depth: int
    spawn_delegate: Callable[[dict], str]
    enable_delegate: bool = False
    repo_index: object | None = None
    patch_journal: object | None = None
    execution_backend: object | None = None
    enable_subagents: bool = False
    spawn_subagents: Callable[[dict], str] | None = None
    spawn_coding_workflow: Callable[[dict], str] | None = None

    def path(self, raw_path):
        return self.path_resolver(str(raw_path))

    def shell_env(self):
        return self.shell_env_provider()
