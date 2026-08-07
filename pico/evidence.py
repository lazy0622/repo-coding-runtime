"""Structured research evidence for Pico V2 supervisors."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .workspace import clip, now


MAX_EVIDENCE_ITEMS = 24
MAX_TEXT_ITEMS = 20


def _text(value, limit=800):
    return clip(str(value or "").strip(), limit)


def _list_text(value, limit=MAX_TEXT_ITEMS, item_limit=500):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = _text(item, item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _bounded_confidence(value, default=0.4):
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = default
    return max(0.0, min(1.0, confidence))


def _candidate_json(text):
    """Find a JSON object in a model final answer without trusting prose."""

    value = str(text or "").strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, ValueError):
        pass
    start = value.find("{")
    end = value.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(value[start : end + 1])
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_evidence_items(value, workspace_root=None):
    if not isinstance(value, list):
        return []
    root = Path(workspace_root).resolve() if workspace_root is not None else None
    result = []
    seen = set()
    for item in value[:MAX_EVIDENCE_ITEMS]:
        if isinstance(item, str):
            item = {"claim": item}
        if not isinstance(item, dict):
            continue
        raw_path = _text(item.get("path", ""), 300)
        line_start = item.get("line_start", item.get("line", 0))
        line_end = item.get("line_end", line_start)
        try:
            line_start = max(0, int(line_start or 0))
            line_end = max(line_start, int(line_end or line_start))
        except (TypeError, ValueError):
            line_start, line_end = 0, 0
        path_valid = True
        if root is not None and raw_path:
            candidate = (root / raw_path).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                path_valid = False
        normalized = {
            "path": raw_path,
            "line_start": line_start,
            "line_end": line_end,
            "symbol": _text(item.get("symbol", ""), 240),
            "claim": _text(item.get("claim", item.get("text", item.get("reason", ""))), 800),
            "kind": _text(item.get("kind", "code"), 80) or "code",
            "confidence": _bounded_confidence(item.get("confidence", 0.7), default=0.7),
            "path_valid": path_valid,
        }
        key = (
            normalized["path"],
            normalized["line_start"],
            normalized["line_end"],
            normalized["symbol"],
            normalized["claim"],
        )
        if not normalized["claim"] or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


@dataclass
class EvidenceBundle:
    """A bounded, reviewable result returned by one research sub-agent."""

    task_id: str = ""
    summary: str = ""
    findings: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    confidence: float = 0.4
    raw_answer: str = ""
    parsed: bool = False
    created_at: str = field(default_factory=now)

    @classmethod
    def from_answer(cls, answer, task_id="", workspace_root=None):
        raw_answer = _text(answer, 2400)
        payload = _candidate_json(raw_answer)
        if payload is None:
            summary = _text(raw_answer, 900) or "The sub-agent returned no usable evidence."
            return cls(
                task_id=str(task_id),
                summary=summary,
                findings=[summary],
                confidence=0.35,
                raw_answer=raw_answer,
                parsed=False,
            )
        summary = _text(payload.get("summary", payload.get("conclusion", "")), 900)
        findings = _list_text(payload.get("findings", []))
        if not summary and findings:
            summary = findings[0]
        if not summary:
            summary = "The sub-agent returned structured evidence without a summary."
        if not findings:
            findings = [summary]
        return cls(
            task_id=str(task_id),
            summary=summary,
            findings=findings,
            evidence=_normalize_evidence_items(payload.get("evidence", []), workspace_root),
            risks=_list_text(payload.get("risks", [])),
            recommendations=_list_text(payload.get("recommendations", payload.get("next_steps", []))),
            confidence=_bounded_confidence(payload.get("confidence", 0.7), default=0.7),
            raw_answer=raw_answer,
            parsed=True,
        )

    @classmethod
    def from_dict(cls, value, task_id="", workspace_root=None):
        value = dict(value or {})
        return cls(
            task_id=str(value.get("task_id", task_id)),
            summary=_text(value.get("summary", ""), 900),
            findings=_list_text(value.get("findings", [])),
            evidence=_normalize_evidence_items(value.get("evidence", []), workspace_root),
            risks=_list_text(value.get("risks", [])),
            recommendations=_list_text(value.get("recommendations", [])),
            confidence=_bounded_confidence(value.get("confidence", 0.4)),
            raw_answer=_text(value.get("raw_answer", ""), 2400),
            parsed=bool(value.get("parsed", False)),
            created_at=_text(value.get("created_at", ""), 80) or now(),
        )

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "summary": self.summary,
            "findings": list(self.findings),
            "evidence": list(self.evidence),
            "risks": list(self.risks),
            "recommendations": list(self.recommendations),
            "confidence": self.confidence,
            "raw_answer": self.raw_answer,
            "parsed": self.parsed,
            "created_at": self.created_at,
        }


def aggregate_evidence(bundles, goal=""):
    """Deterministically merge child evidence for the Supervisor result."""

    normalized = [item if isinstance(item, EvidenceBundle) else EvidenceBundle.from_dict(item) for item in bundles]
    findings = []
    risks = []
    recommendations = []
    evidence = []
    seen_text = set()
    seen_evidence = set()
    for bundle in normalized:
        for collection, target in (
            (bundle.findings, findings),
            (bundle.risks, risks),
            (bundle.recommendations, recommendations),
        ):
            for item in collection:
                if item not in seen_text:
                    target.append(item)
                    seen_text.add(item)
        for item in bundle.evidence:
            key = (
                item.get("path", ""),
                item.get("line_start", 0),
                item.get("line_end", 0),
                item.get("symbol", ""),
                item.get("claim", ""),
            )
            if key not in seen_evidence:
                evidence.append({**item, "source_task_id": bundle.task_id})
                seen_evidence.add(key)
    confidence = round(
        sum(bundle.confidence for bundle in normalized) / len(normalized),
        3,
    ) if normalized else 0.0
    summaries = [bundle.summary for bundle in normalized if bundle.summary]
    summary = _text(
        f"{goal}: " + " ".join(summaries) if goal and summaries else " ".join(summaries),
        1600,
    )
    return {
        "summary": summary or "No completed research evidence was available.",
        "findings": findings[:MAX_TEXT_ITEMS],
        "evidence": evidence[:MAX_EVIDENCE_ITEMS],
        "risks": risks[:MAX_TEXT_ITEMS],
        "recommendations": recommendations[:MAX_TEXT_ITEMS],
        "confidence": confidence,
        "source_task_ids": [bundle.task_id for bundle in normalized],
    }


def dependency_context(bundles):
    """Render bounded structured evidence for a dependent child prompt."""

    return json.dumps(
        [bundle.to_dict() if isinstance(bundle, EvidenceBundle) else bundle for bundle in bundles],
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )[:3200]
