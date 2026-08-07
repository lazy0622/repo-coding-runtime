"""Strict unified-diff application and guarded rollback support."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .atomic_io import replace_with_retry


class PatchError(ValueError):
    """Raised when a patch is malformed, unsafe, or does not match the file."""


@dataclass(frozen=True)
class PatchHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[str, ...]


@dataclass(frozen=True)
class FilePatch:
    path: str
    hunks: tuple[PatchHunk, ...]


def _sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_relative_path(root, raw_path):
    value = str(raw_path or "").strip().replace("\\", "/")
    if not value or value == "/dev/null":
        raise PatchError("patch must target an existing workspace file")
    if value.startswith("a/") or value.startswith("b/"):
        value = value[2:]
    candidate = (Path(root) / value).resolve()
    try:
        relative = candidate.relative_to(Path(root).resolve())
    except ValueError as exc:
        raise PatchError(f"path escapes workspace: {raw_path}") from exc
    if any(part in {".git", ".pico"} for part in relative.parts):
        raise PatchError("patch cannot modify protected workspace metadata")
    return relative.as_posix()


_HUNK_HEADER = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)?$"
)


def _diff_path(header):
    value = str(header[4:]).strip()
    return value.split("\t", 1)[0].strip()


def parse_unified_diff(patch_text):
    text = str(patch_text or "")
    if not text.strip():
        raise PatchError("patch must not be empty")
    lines = text.splitlines()
    patches = []
    index = 0
    while index < len(lines):
        if not lines[index].startswith("--- "):
            index += 1
            continue
        if index + 1 >= len(lines) or not lines[index + 1].startswith("+++ "):
            raise PatchError("unified diff is missing the +++ file header")
        old_path = _diff_path(lines[index])
        new_path = _diff_path(lines[index + 1])
        if old_path == "/dev/null" or new_path == "/dev/null":
            raise PatchError("file creation and deletion patches are not supported; use write_file for new files")
        raw_old = old_path[2:] if old_path.startswith("a/") else old_path
        raw_new = new_path[2:] if new_path.startswith("b/") else new_path
        if raw_old.replace("\\", "/") != raw_new.replace("\\", "/"):
            raise PatchError("rename patches are not supported")

        hunks = []
        index += 2
        while index < len(lines) and not lines[index].startswith("--- "):
            match = _HUNK_HEADER.match(lines[index])
            if not match:
                index += 1
                continue
            old_start = int(match.group(1))
            old_count = int(match.group(2) or "1")
            new_start = int(match.group(3))
            new_count = int(match.group(4) or "1")
            index += 1
            hunk_lines = []
            while index < len(lines) and not lines[index].startswith("@@ ") and not lines[index].startswith("--- "):
                line = lines[index]
                if line.startswith((" ", "+", "-")):
                    hunk_lines.append(line)
                elif line.startswith("\\ No newline at end of file"):
                    pass
                else:
                    raise PatchError(f"invalid unified diff line: {line!r}")
                index += 1
            actual_old = sum(1 for line in hunk_lines if line[0] in {" ", "-"})
            actual_new = sum(1 for line in hunk_lines if line[0] in {" ", "+"})
            if actual_old != old_count or actual_new != new_count:
                raise PatchError(
                    f"hunk line count mismatch: expected -{old_count}/+{new_count}, "
                    f"got -{actual_old}/+{actual_new}"
                )
            hunks.append(PatchHunk(old_start, old_count, new_start, new_count, tuple(hunk_lines)))
        if not hunks:
            raise PatchError("unified diff contains no hunks")
        patches.append(FilePatch(raw_new.replace("\\", "/"), tuple(hunks)))
    if not patches:
        raise PatchError("unified diff contains no file patches")
    return tuple(patches)


class PatchJournal:
    """Persist small patch backups under the ignored .pico directory."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.directory = self.root / ".pico" / "patches"

    def _manifest_path(self, backup_id):
        value = str(backup_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", value):
            raise PatchError("invalid backup_id")
        return self.directory / f"{value}.json"

    @staticmethod
    def _atomic_write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="") as handle:
                handle.write(text)
            replace_with_retry(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def create(self, changes):
        backup_id = f"patch-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:10]}"
        manifest = {
            "backup_id": backup_id,
            "status": "active",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "files": [
                {
                    "path": item["path"],
                    "before": item["before"],
                    "before_sha256": _sha256_text(item["before"]),
                    "after_sha256": _sha256_text(item["after"]),
                    "existed": bool(item.get("existed", True)),
                }
                for item in changes
            ],
        }
        self._atomic_write(self._manifest_path(backup_id), json.dumps(manifest, ensure_ascii=False, indent=2))
        return backup_id

    def load(self, backup_id):
        path = self._manifest_path(backup_id)
        if not path.is_file():
            raise PatchError(f"backup not found: {backup_id}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PatchError(f"invalid backup manifest: {backup_id}") from exc

    def rollback(self, backup_id):
        manifest = self.load(backup_id)
        if manifest.get("status") != "active":
            raise PatchError(f"backup is not active: {backup_id}")
        files = manifest.get("files", [])
        for item in files:
            relative = _safe_relative_path(self.root, item.get("path"))
            path = self.root / relative
            if not path.is_file():
                raise PatchError(f"cannot rollback changed file: {relative}")
            current = path.read_text(encoding="utf-8")
            if _sha256_text(current) != item.get("after_sha256"):
                raise PatchError(f"file changed after patch; refusing rollback: {relative}")
        for item in files:
            relative = _safe_relative_path(self.root, item.get("path"))
            path = self.root / relative
            with path.open("w", encoding="utf-8", newline="") as handle:
                handle.write(item.get("before", ""))
        manifest["status"] = "rolled_back"
        manifest["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
        self._atomic_write(self._manifest_path(backup_id), json.dumps(manifest, ensure_ascii=False, indent=2))
        return {"backup_id": backup_id, "status": "rolled_back", "files": [item["path"] for item in files]}


def _apply_file_patch(original, file_patch):
    newline = "\r\n" if "\r\n" in original else "\n"
    trailing_newline = original.endswith(("\n", "\r"))
    lines = original.splitlines()
    offset = 0
    for hunk in file_patch.hunks:
        old_lines = [line[1:] for line in hunk.lines if line[0] in {" ", "-"}]
        new_lines = [line[1:] for line in hunk.lines if line[0] in {" ", "+"}]
        position = max(0, hunk.old_start - 1 + offset)
        actual = lines[position : position + len(old_lines)]
        if actual != old_lines:
            raise PatchError(
                f"patch context mismatch in {file_patch.path} near line {hunk.old_start}"
            )
        lines[position : position + len(old_lines)] = new_lines
        offset += len(new_lines) - len(old_lines)
    result = newline.join(lines)
    if trailing_newline and result:
        result += newline
    return result


def apply_unified_diff(root, patch_text, journal=None):
    root = Path(root).resolve()
    parsed = parse_unified_diff(patch_text)
    prepared = []
    seen = set()
    for file_patch in parsed:
        relative = _safe_relative_path(root, file_patch.path)
        if relative in seen:
            raise PatchError(f"duplicate file patch: {relative}")
        seen.add(relative)
        path = root / relative
        if not path.is_file():
            raise PatchError(f"patch target is not a file: {relative}")
        try:
            before = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise PatchError(f"cannot read patch target: {relative}") from exc
        after = _apply_file_patch(before, file_patch)
        if before != after:
            prepared.append({"path": relative, "before": before, "after": after, "existed": True})

    if not prepared:
        return {"status": "no_change", "backup_id": "", "files": []}

    journal = journal or PatchJournal(root)
    backup_id = journal.create(prepared)
    written = []
    try:
        for item in prepared:
            path = root / item["path"]
            with path.open("w", encoding="utf-8", newline="") as handle:
                handle.write(item["after"])
            written.append(item)
    except Exception:
        for item in reversed(written):
            try:
                with (root / item["path"]).open("w", encoding="utf-8", newline="") as handle:
                    handle.write(item["before"])
            except OSError:
                pass
        raise
    return {"status": "applied", "backup_id": backup_id, "files": [item["path"] for item in prepared]}


def preview_file_diff(root, path, new_content):
    root = Path(root).resolve()
    relative = _safe_relative_path(root, path)
    target = root / relative
    if not target.is_file():
        raise PatchError(f"diff target is not a file: {relative}")
    old_content = target.read_text(encoding="utf-8")
    new_content = str(new_content)
    diff = "".join(
        difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
        )
    )
    return {
        "path": relative,
        "changed": old_content != new_content,
        "old_sha256": _sha256_text(old_content),
        "new_sha256": _sha256_text(new_content),
        "diff": diff,
    }
