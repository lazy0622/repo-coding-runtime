import json

from pico.evaluation.swebench_results import parse_official_results


def test_official_results_require_instance_level_grade_and_keep_failures(tmp_path):
    result_path = tmp_path / "official"
    result_path.mkdir()
    (result_path / "results.json").write_text(
        json.dumps(
            {
                "results": [
                    {"instance_id": "owner__repo-1", "resolved": True},
                    {"instance_id": "owner__repo-2", "resolved": False},
                ]
            }
        ),
        encoding="utf-8",
    )

    result = parse_official_results(
        result_path,
        instance_ids=["owner__repo-1", "owner__repo-2"],
    )

    assert result["official_grade_status"] == "graded"
    assert result["official_resolved"] == 1
    assert result["official_resolved_rate"] == 0.5
    assert result["official_failed_instances"] == ["owner__repo-2"]


def test_official_results_do_not_fill_rate_for_partial_or_missing_grade(tmp_path):
    result_path = tmp_path / "partial.json"
    result_path.write_text(
        json.dumps({"owner__repo-1": {"status": "resolved"}}),
        encoding="utf-8",
    )

    result = parse_official_results(
        result_path,
        instance_ids=["owner__repo-1", "owner__repo-2"],
    )

    assert result["official_grade_status"] == "partial"
    assert result["official_resolved_rate"] is None
    assert result["missing_instances"] == ["owner__repo-2"]


def test_missing_official_results_are_not_run(tmp_path):
    result = parse_official_results(tmp_path / "does-not-exist.json", ["owner__repo-1"])

    assert result["official_grade_status"] == "not_run"
    assert result["official_resolved"] == 0
    assert result["official_resolved_rate"] is None
