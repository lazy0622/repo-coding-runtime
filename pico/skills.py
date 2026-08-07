"""Project and user skill discovery with deterministic, lazy selection."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path


SKILL_FILENAME = "SKILL.md"
SKILL_RENDER_LIMIT = 4000
SKILL_FILE_SIZE_LIMIT = 128 * 1024


def _tokens(value):
    return {token.lower() for token in re.findall(r"[A-Za-z0-9_\-\u4e00-\u9fff]+", str(value))}


def _parse_scalar(value):
    value = str(value).strip()
    if value.startswith("[") and value.endswith("]"):
        return [item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()]
    return value.strip("'\"")


def _parse_frontmatter(text):
    text = str(text)
    if not text.startswith("---"):
        return {}, text.strip()
    lines = text.splitlines()
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise ValueError("skill frontmatter is missing the closing ---")

    metadata = {}
    current_list = None
    for raw_line in lines[1:end]:
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        if stripped.startswith("-") and current_list:
            metadata.setdefault(current_list, []).append(stripped[1:].strip().strip("'\""))
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        parsed = _parse_scalar(value)
        if parsed == "":
            metadata[key] = []
            current_list = key
        else:
            metadata[key] = parsed
            current_list = None
    return metadata, "\n".join(lines[end + 1 :]).strip()


def _as_list(value):
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


@dataclass(frozen=True)
class SkillSpec:
    name: str
    description: str
    version: str
    instructions: str
    path: Path
    tools: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    risk_level: str = "low"

    @classmethod
    def load(cls, path):
        path = Path(path)
        if path.stat().st_size > SKILL_FILE_SIZE_LIMIT:
            raise ValueError(f"skill file exceeds {SKILL_FILE_SIZE_LIMIT} bytes")
        metadata, instructions = _parse_frontmatter(path.read_text(encoding="utf-8"))
        name = str(metadata.get("name") or path.parent.name).strip()
        description = str(metadata.get("description") or "").strip()
        if not name:
            raise ValueError("skill name must not be empty")
        if not description:
            raise ValueError(f"skill {name} is missing a description")
        if not instructions:
            raise ValueError(f"skill {name} has no instructions")
        return cls(
            name=name,
            description=description,
            version=str(metadata.get("version") or "1.0.0").strip(),
            instructions=instructions,
            path=path.resolve(),
            tools=tuple(_as_list(metadata.get("tools"))),
            tags=tuple(_as_list(metadata.get("tags"))),
            risk_level=str(metadata.get("risk_level") or "low").strip(),
        )

    def summary(self):
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "tools": list(self.tools),
            "tags": list(self.tags),
            "risk_level": self.risk_level,
            "path": str(self.path),
        }


class SkillRegistry:
    def __init__(self, skills=(), diagnostics=()):
        self._skills = {skill.name: skill for skill in skills}
        self.diagnostics = list(diagnostics)

    @classmethod
    def discover(cls, roots):
        skills = {}
        diagnostics = []
        for root in roots or ():
            root = Path(root).expanduser()
            if not root.exists():
                continue
            discovery_root = (root.parent if root.is_file() else root).resolve()
            candidates = [root] if root.is_file() and root.name == SKILL_FILENAME else sorted(root.rglob(SKILL_FILENAME))
            for path in candidates:
                try:
                    resolved_path = path.resolve()
                    resolved_path.relative_to(discovery_root)
                    if path.is_symlink() and resolved_path != path.absolute():
                        raise ValueError("skill symlink is not allowed")
                    skill = SkillSpec.load(path)
                except Exception as exc:
                    diagnostics.append({"level": "error", "path": str(path), "message": str(exc)})
                    continue
                if skill.name in skills:
                    diagnostics.append(
                        {
                            "level": "warning",
                            "path": str(path),
                            "message": f"duplicate skill ignored: {skill.name}",
                        }
                    )
                    continue
                skills[skill.name] = skill
        return cls(skills.values(), diagnostics=diagnostics)

    def __len__(self):
        return len(self._skills)

    def names(self):
        return tuple(sorted(self._skills))

    def get(self, name):
        return self._skills.get(str(name))

    def signature(self):
        # Runtime identity must survive moving the same project into an isolated
        # worktree. Absolute discovery paths are useful diagnostics, but they are
        # not part of a skill's behavior and therefore must not affect the hash.
        payload = []
        for name in sorted(self._skills):
            skill = self._skills[name]
            payload.append(
                {
                    "name": skill.name,
                    "description": skill.description,
                    "version": skill.version,
                    "tools": list(skill.tools),
                    "tags": list(skill.tags),
                    "risk_level": skill.risk_level,
                    "instructions": skill.instructions,
                }
            )
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def select(self, query, available_tools=(), limit=3):
        query_text = str(query).strip()
        query_tokens = _tokens(query_text)
        available = set(available_tools or ())
        ranked = []
        for skill in self._skills.values():
            if skill.tools and not set(skill.tools).issubset(available):
                continue
            name_tokens = _tokens(skill.name)
            metadata_tokens = name_tokens | _tokens(skill.description) | _tokens(" ".join(skill.tags))
            overlap = len(query_tokens & metadata_tokens)
            explicit = int(skill.name.lower() in query_text.lower())
            if not explicit and overlap == 0:
                continue
            ranked.append(((explicit, overlap, skill.name), skill))
        ranked.sort(key=lambda item: (-item[0][0], -item[0][1], item[0][2]))
        return [skill for _, skill in ranked[: max(0, int(limit))]]

    @staticmethod
    def render(skills, limit=SKILL_RENDER_LIMIT):
        skills = list(skills or ())
        if not skills:
            return ""
        lines = ["Selected skills:"]
        for skill in skills:
            lines.extend(
                [
                    f"## {skill.name} (v{skill.version})",
                    f"Description: {skill.description}",
                    f"Allowed tools: {', '.join(skill.tools) or 'runtime policy'}",
                    skill.instructions,
                ]
            )
        rendered = "\n".join(lines).strip()
        if len(rendered) <= limit:
            return rendered
        return rendered[: max(0, limit - 3)] + "..."
