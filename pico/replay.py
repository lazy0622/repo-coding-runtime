"""Human-readable replay and summary of a Pico trace JSONL artifact."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


def resolve_trace_path(value, cwd="."):
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path(cwd) / path
    if path.is_dir():
        path = path / "trace.jsonl"
    if path.exists():
        return path
    candidate = Path(cwd) / ".pico" / "runs" / str(value) / "trace.jsonl"
    return candidate


def load_trace(path):
    path = Path(path)
    events = []
    if not path.exists():
        raise FileNotFoundError(f"trace not found: {path}")
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            events.append({"event": "malformed_trace_line", "line_number": line_number})
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def summarize_trace(events):
    events = list(events or [])
    event_counts = Counter(str(event.get("event", "unknown")) for event in events)
    tool_events = [
        event
        for event in events
        if event.get("event") in {"tool_finished", "tool_completed", "tool_executed"}
        or event.get("event_type") in {"tool.finished", "tool.completed"}
    ]
    verification_events = [event for event in events if event.get("event") == "verification_finished"]
    state_changes = [
        {
            "from": event.get("previous_phase", ""),
            "to": event.get("current_phase", ""),
            "reason": event.get("reason", ""),
        }
        for event in events
        if event.get("event") == "state_changed"
    ]
    return {
        "event_count": len(events),
        "event_counts": dict(sorted(event_counts.items())),
        "tool_calls": [
            {
                "name": event.get("name", ""),
                "status": event.get("tool_status", ""),
                "error_code": event.get("tool_error_code", ""),
                "duration_ms": event.get("duration_ms"),
            }
            for event in tool_events
        ],
        "verification": [
            {
                "status": event.get("status", ""),
                "passed": event.get("passed"),
                "exit_code": event.get("exit_code"),
                "duration_ms": event.get("duration_ms"),
                "error_code": event.get("error_code", ""),
            }
            for event in verification_events
        ],
        "state_changes": state_changes,
        "final_event": events[-1].get("event", "") if events else "",
    }


def render_replay(path_or_events, include_json=False):
    if isinstance(path_or_events, (str, Path)):
        path = Path(path_or_events)
        events = load_trace(path)
        title = str(path)
    else:
        path = None
        events = list(path_or_events or [])
        title = "trace"
    summary = summarize_trace(events)
    lines = [
        f"Pico replay: {title}",
        f"Events: {summary['event_count']}",
        f"Final event: {summary['final_event'] or '-'}",
        "",
        "Timeline:",
    ]
    for index, event in enumerate(events, start=1):
        name = str(event.get("event", "unknown"))
        detail = ""
        if name in {"tool_finished", "tool_completed", "tool_executed"}:
            detail = f" {event.get('name', '')} [{event.get('tool_status', '')}]"
        elif name == "state_changed":
            detail = f" {event.get('previous_phase', '')} -> {event.get('current_phase', '')} ({event.get('reason', '')})"
        elif name == "verification_finished":
            detail = f" {event.get('status', '')} exit={event.get('exit_code', '')}"
        elif name == "plan_updated":
            detail = f" {event.get('source', '')}"
        lines.append(f"{index:>3}. {name}{detail}")
    lines.extend(
        [
            "",
            f"Tool calls: {len(summary['tool_calls'])}",
            f"Verification runs: {len(summary['verification'])}",
        ]
    )
    if include_json:
        lines.extend(["", json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)])
    return "\n".join(lines)

