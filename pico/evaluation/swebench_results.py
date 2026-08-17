"""Strict separation between SWE-bench generation and official grading.

The generator can produce a patch and a JSONL prediction without proving that
the issue is solved.  This module only turns already-produced official grader
artifacts into resolved counts; it never treats agent completion or a
non-empty patch as an official result.
"""

from __future__ import annotations

import json
from pathlib import Path


OFFICIAL_GRADE_SCHEMA_VERSION = "swebench-official-grade-v1"
_TRUE_VALUES = {"true", "1", "yes", "passed", "pass", "resolved", "success", "successful"}
_FALSE_VALUES = {
    "false",
    "0",
    "no",
    "failed",
    "fail",
    "not_resolved",
    "not resolved",
    "unresolved",
}


def _coerce_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    text = str(value or "").strip().lower().replace("-", "_")
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    return None


def _instance_id(value):
    if isinstance(value, str) and ("__" in value or "-" in value):
        return value
    return ""


def _record_from_mapping(mapping, hinted_id=""):
    if not isinstance(mapping, dict):
        return None
    instance_id = str(
        mapping.get("instance_id")
        or mapping.get("instance")
        or mapping.get("id")
        or hinted_id
        or ""
    ).strip()
    if not instance_id:
        return None
    resolved = None
    resolved_key = ""
    for key in ("resolved", "is_resolved", "official_resolved", "status", "result"):
        if key in mapping:
            candidate = _coerce_bool(mapping[key])
            if candidate is not None:
                resolved = candidate
                resolved_key = key
                break
    if resolved is None:
        return None
    return {
        "instance_id": instance_id,
        "resolved": resolved,
        "source_key": resolved_key,
    }


def _extract_records(value, records):
    if isinstance(value, list):
        for item in value:
            _extract_records(item, records)
        return
    if not isinstance(value, dict):
        return
    direct = _record_from_mapping(value)
    if direct:
        records.append(direct)
    for key, item in value.items():
        hinted_id = _instance_id(str(key))
        nested = _record_from_mapping(item, hinted_id=hinted_id)
        if nested:
            records.append(nested)
        if isinstance(item, (dict, list)):
            _extract_records(item, records)


def _candidate_files(path):
    path = Path(path)
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(
            item
            for item in path.rglob("*")
            if item.is_file() and item.suffix.lower() in {".json", ".jsonl"}
        )
    return []


def parse_official_results(path, instance_ids=None):
    """Parse official output while keeping missing/partial grade explicit."""

    path = Path(path)
    expected = [str(item) for item in (instance_ids or [])]
    if not path.exists():
        return {
            "schema_version": OFFICIAL_GRADE_SCHEMA_VERSION,
            "official_grade_status": "not_run",
            "source_path": str(path),
            "expected_instances": expected,
            "graded_instances": 0,
            "official_resolved": 0,
            "official_resolved_rate": None,
            "official_failed_instances": [],
            "missing_instances": expected,
            "records": [],
            "diagnostics": ["official grader output was not found"],
        }

    records = []
    diagnostics = []
    source_files = []
    for file_path in _candidate_files(path):
        source_files.append(str(file_path))
        try:
            text = file_path.read_text(encoding="utf-8")
            if file_path.suffix.lower() == ".jsonl":
                for line in text.splitlines():
                    if line.strip():
                        _extract_records(json.loads(line), records)
            else:
                _extract_records(json.loads(text), records)
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            diagnostics.append(f"could not parse {file_path.name}: {exc}")

    unique = {}
    for record in records:
        unique[record["instance_id"]] = record
    if expected:
        unique = {key: value for key, value in unique.items() if key in set(expected)}
    ordered = [unique[key] for key in sorted(unique)]
    missing = sorted(set(expected) - set(unique))
    resolved_ids = sorted(record["instance_id"] for record in ordered if record["resolved"])
    failed_ids = sorted(record["instance_id"] for record in ordered if not record["resolved"])
    complete = bool(ordered) and not missing if expected else bool(ordered)
    status = "graded" if complete else ("partial" if ordered else "failed")
    denominator = len(expected) if expected else len(ordered)
    rate = (len(resolved_ids) / denominator) if denominator and complete else None
    if not ordered and not diagnostics:
        diagnostics.append("no instance-level official grade records were found")
    return {
        "schema_version": OFFICIAL_GRADE_SCHEMA_VERSION,
        "official_grade_status": status,
        "source_path": str(path),
        "source_files": source_files,
        "expected_instances": expected,
        "graded_instances": len(ordered),
        "official_resolved": len(resolved_ids),
        "official_resolved_rate": rate,
        "official_resolved_instances": resolved_ids,
        "official_failed_instances": failed_ids,
        "missing_instances": missing,
        "records": ordered,
        "diagnostics": diagnostics,
    }


__all__ = ["OFFICIAL_GRADE_SCHEMA_VERSION", "parse_official_results"]
