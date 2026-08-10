from pico.evaluation.ablation import (
    compare_policy_artifacts,
    render_policy_ablation_markdown,
)


def test_policy_ablation_reports_directional_delta():
    baseline = {
        "summary": {"total_tasks": 2, "passed": 1, "pass_rate": 0.5, "verifier_pass_rate": 0.5},
        "execution_metrics": {"patch_generation_rate": 0.5, "average_first_edit_step": 3},
    }
    enhanced = {
        "summary": {"total_tasks": 2, "passed": 2, "pass_rate": 1.0, "verifier_pass_rate": 1.0},
        "execution_metrics": {"patch_generation_rate": 1.0, "average_first_edit_step": 2},
    }

    comparison = compare_policy_artifacts(baseline, enhanced)

    assert comparison["delta"]["pass_rate"] == 0.5
    assert comparison["delta"]["average_first_edit_step"] == -1
    assert "not live-model quality" in render_policy_ablation_markdown(comparison)
