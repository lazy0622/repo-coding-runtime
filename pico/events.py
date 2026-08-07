"""Canonical run-event schema used by trace, evaluation, and replay tooling.

The original Pico trace stored only an ``event`` name plus an arbitrary payload.
V1 keeps that field for backwards compatibility and adds stable identifiers and a
dot-separated ``event_type`` that downstream tooling can evolve against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


EVENT_SCHEMA_VERSION = 1


def canonical_event_type(name: str) -> str:
    """Return the canonical dot-separated form of a legacy event name."""

    return str(name or "unknown").strip().replace("_", ".") or "unknown"


@dataclass(frozen=True)
class RunEvent:
    event: str
    event_type: str
    run_id: str
    task_id: str
    created_at: str
    status: str = ""
    phase: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = EVENT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        result = dict(self.payload)
        result.update(
            {
                "event": self.event,
                "event_type": self.event_type,
                "event_schema_version": self.schema_version,
                "run_id": self.run_id,
                "task_id": self.task_id,
                "created_at": self.created_at,
            }
        )
        if self.status:
            result["status"] = self.status
        if self.phase:
            result["phase"] = self.phase
        return result


def build_run_event(task_state, event: str, created_at: str, payload=None) -> dict[str, Any]:
    """Build one backwards-compatible, schema-versioned trace event."""

    return RunEvent(
        event=str(event),
        event_type=canonical_event_type(event),
        run_id=str(getattr(task_state, "run_id", "")),
        task_id=str(getattr(task_state, "task_id", "")),
        created_at=str(created_at),
        status=str(getattr(task_state, "status", "")),
        phase=str(getattr(task_state, "phase", "")),
        payload=dict(payload or {}),
    ).to_dict()
