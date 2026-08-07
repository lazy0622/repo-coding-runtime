from pico.evidence import EvidenceBundle, aggregate_evidence


def test_evidence_bundle_parses_json_and_normalizes_fields(tmp_path):
    bundle = EvidenceBundle.from_answer(
        '{"summary":"Found the entry point.","findings":["main is called"],"evidence":[{"path":"app.py","line":4,"claim":"entry"}],"confidence":2}',
        task_id="inspect",
        workspace_root=tmp_path,
    )

    assert bundle.parsed is True
    assert bundle.task_id == "inspect"
    assert bundle.evidence[0]["line_start"] == 4
    assert bundle.confidence == 1.0


def test_evidence_bundle_falls_back_for_plain_text():
    bundle = EvidenceBundle.from_answer("The service is small.", task_id="inspect")

    assert bundle.parsed is False
    assert bundle.findings == ["The service is small."]
    assert bundle.confidence < 0.5


def test_aggregate_evidence_deduplicates_findings_and_attaches_source():
    first = EvidenceBundle.from_answer(
        '{"summary":"A","findings":["same"],"evidence":[{"path":"a.py","line":1,"claim":"x"}],"confidence":0.8}',
        task_id="a",
    )
    second = EvidenceBundle.from_answer(
        '{"summary":"B","findings":["same","other"],"evidence":[{"path":"a.py","line":1,"claim":"x"}],"confidence":0.6}',
        task_id="b",
    )

    summary = aggregate_evidence([first, second], goal="Inspect")

    assert summary["findings"] == ["same", "other"]
    assert len(summary["evidence"]) == 1
    assert summary["evidence"][0]["source_task_id"] == "a"
    assert summary["confidence"] == 0.7
