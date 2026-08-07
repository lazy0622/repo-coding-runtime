from pathlib import Path

from pico.skills import SkillRegistry


SKILL_TEXT = """---
name: python-bugfix
description: Diagnose and fix Python test failures.
version: 1.2.0
tools: [read_file, apply_patch]
tags: [python, pytest, debugging]
risk_level: medium
---
Read the failing test first, then make the smallest verified patch.
"""


def test_skill_registry_discovers_selects_and_renders_relevant_skill(tmp_path):
    skill_file = tmp_path / "python-bugfix" / "SKILL.md"
    skill_file.parent.mkdir()
    skill_file.write_text(SKILL_TEXT, encoding="utf-8")
    registry = SkillRegistry.discover([tmp_path])

    selected = registry.select(
        "Please debug this Python pytest failure",
        available_tools={"read_file", "apply_patch"},
    )

    assert registry.names() == ("python-bugfix",)
    assert [skill.name for skill in selected] == ["python-bugfix"]
    assert "smallest verified patch" in registry.render(selected)


def test_skill_selection_respects_required_tools(tmp_path):
    skill_file = tmp_path / "python-bugfix" / "SKILL.md"
    skill_file.parent.mkdir()
    skill_file.write_text(SKILL_TEXT, encoding="utf-8")
    registry = SkillRegistry.discover([tmp_path])

    assert registry.select("debug Python", available_tools={"read_file"}) == []


def test_skill_signature_is_independent_of_discovery_path(tmp_path):
    first = tmp_path / "first" / "python-bugfix" / "SKILL.md"
    second = tmp_path / "second" / "python-bugfix" / "SKILL.md"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text(SKILL_TEXT, encoding="utf-8")
    second.write_text(SKILL_TEXT, encoding="utf-8")

    assert SkillRegistry.discover([first]).signature() == SkillRegistry.discover([second]).signature()


def test_invalid_skill_is_reported_without_breaking_discovery(tmp_path):
    path = Path(tmp_path) / "bad" / "SKILL.md"
    path.parent.mkdir()
    path.write_text("---\nname: bad\n---\n", encoding="utf-8")

    registry = SkillRegistry.discover([tmp_path])

    assert len(registry) == 0
    assert registry.diagnostics[0]["level"] == "error"
