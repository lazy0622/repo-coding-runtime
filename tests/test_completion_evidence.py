from pico.evaluation.evaluator import summarize_rows, render_benchmark_markdown


def test_final_answer_does_not_imply_verifier_success():
    summary = summarize_rows([
        {"stop_reason": "final_answer_returned", "verifier_passed": False},
        {"stop_reason": "final_answer_returned", "verifier_passed": True},
        {"stop_reason": "final_answer_returned"},
        {"stop_reason": "budget_exceeded", "verifier_passed": False},
    ])
    assert summary["final_answer_count"] == 3
    assert summary["verified_final_answer_count"] == 2
    assert summary["final_answer_with_failed_verifier_count"] == 1
    assert summary["final_answer_with_failed_verifier_rate"] == 0.5
    report = render_benchmark_markdown({"summary": summary})
    assert "Final answers with failed verifier: 1" in report


def test_missing_verifier_is_unknown_not_zero_failure_rate():
    for rows in ([], [{"stop_reason": "final_answer_returned"}]):
        assert summarize_rows(rows)["final_answer_with_failed_verifier_rate"] is None
