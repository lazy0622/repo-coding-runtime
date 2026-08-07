"""Session JSON persistence."""

import json
import tempfile
from pathlib import Path

from .atomic_io import replace_with_retry


class SessionStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, session_id):
        return self.root / f"{session_id}.json"

    def save(self, session):
        path = self.path(session["id"])
        temp_name = ""
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                delete=False,
                dir=str(path.parent),
                prefix=path.name + ".",
                suffix=".tmp",
            ) as handle:
                json.dump(session, handle, indent=2)
                handle.write("\n")
                temp_name = handle.name
            replace_with_retry(temp_name, path)
        finally:
            if temp_name and Path(temp_name).exists():
                Path(temp_name).unlink()
        return path

    def load(self, session_id):
        return json.loads(self.path(session_id).read_text(encoding="utf-8"))

    def latest(self):
        files = sorted(self.root.glob("*.json"), key=lambda path: path.stat().st_mtime)
        return files[-1].stem if files else None
