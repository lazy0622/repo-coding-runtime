import json
import subprocess
import sys

from pico.evaluation.swebench_matrix import run_matrix


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def make_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q")
    git(source, "config", "user.email", "benchmark@example.com")
    git(source, "config", "user.name", "Benchmark")
    (source / "service.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(source, "add", "service.py")
    git(source, "commit", "-qm", "base")
    return source, git(source, "rev-parse", "HEAD").stdout.strip()


def test_matrix_runs_both_policy_modes_and_retains_generation_metrics(tmp_path):
    source, commit = make_source(tmp_path)
    manifest = tmp_path / "instances.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "instance_id": "local__matrix-1",
                "repo_path": str(source),
                "base_commit": commit,
                "problem_statement": "Change VALUE to 2.",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    helper = tmp_path / "agent.py"
    helper.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "Path('service.py').write_text('VALUE = 2\\n', encoding='utf-8')\n"
        "run = Path('.pico/runs/run-1')\n"
        "run.mkdir(parents=True)\n"
        "policy = '--execution-policy' in sys.argv and sys.argv[sys.argv.index('--execution-policy') + 1] == 'on'\n"
        "(run / 'report.json').write_text(json.dumps({'status':'completed','tool_steps':3,'execution_policy':{'enabled':policy,'first_edit_step':2,'discovery_tool_steps':1,'verification_tool_steps':1,'verification_repair_count':0,'repeated_tool_rejections':0,'final_verifier_passed':True},'execution_backend':{'mode':'host'}}))\n"
        "(run / 'trace.jsonl').write_text(json.dumps({'event':'model_parsed','completion_metadata':{'input_tokens':10,'output_tokens':2,'total_tokens':12,'cached_tokens':1}})+'\\n')\n",
        encoding="utf-8",
    )

    result = run_matrix(
        selection_path=manifest,
        output_dir=tmp_path / "matrix",
        agent_command=[sys.executable, str(helper)],
        mode="both",
        repetitions=1,
        model_name="test-model",
        temperature=0.1,
        max_agent_steps=4,
        timeout=30,
        repo_root=tmp_path,
    )

    assert result["matrix_manifest"]["evaluation_tier"] == "pilot"
    assert result["generation"]["task_runs"] == 2
    assert result["generation"]["by_mode"]["policy_on"]["agent_completion_rate"] == 1.0
    assert result["generation"]["by_mode"]["policy_off"]["average_total_tokens"] == 12.0
    assert result["ablation"]["policy_on"]["non_empty_patch_rate"] == 1.0
    assert not list((tmp_path / "matrix" / "failures").rglob("*.json"))


def test_matrix_grade_only_writes_separate_official_summary(tmp_path):
    source, commit = make_source(tmp_path)
    manifest = tmp_path / "instances.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "instance_id": "local__matrix-1",
                "repo_path": str(source),
                "base_commit": commit,
                "problem_statement": "Inspect.",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    official = tmp_path / "official.json"
    official.write_text(
        json.dumps({"results": [{"instance_id": "local__matrix-1", "resolved": True}]}),
        encoding="utf-8",
    )

    result = run_matrix(
        selection_path=manifest,
        output_dir=tmp_path / "grade",
        mode="policy_on",
        generate=False,
        official_results=official,
        repo_root=tmp_path,
    )

    assert result["generation"]["task_runs"] == 0
    assert result["official_grade"]["official_grade_status"] == "graded"
    assert result["official_grade"]["official_resolved_rate"] == 1.0
    assert (tmp_path / "grade" / "official_grade_summary.json").is_file()
