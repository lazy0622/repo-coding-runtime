import json
import subprocess
import sys

import pico.evaluation.swebench as swebench_module
from pico.evaluation.swebench import (
    SWEbenchAdapter,
    load_instances,
    official_evaluation_command,
)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def test_swebench_adapter_generates_official_prediction_from_real_git_repo(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q")
    git(source, "config", "user.email", "benchmark@example.com")
    git(source, "config", "user.name", "Benchmark")
    (source / "service.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(source, "add", "service.py")
    git(source, "commit", "-qm", "base")
    commit = git(source, "rev-parse", "HEAD").stdout.strip()
    manifest = tmp_path / "instances.jsonl"
    manifest.write_text(
        json.dumps({"instance_id": "local__repo-1", "repo_path": str(source), "base_commit": commit, "problem_statement": "Change VALUE to 2."}) + "\n",
        encoding="utf-8",
    )
    helper = tmp_path / "edit.py"
    helper.write_text("from pathlib import Path\np=Path('service.py')\np.write_text('VALUE = 2\\n', encoding='utf-8')\n", encoding="utf-8")

    output = tmp_path / "output"
    summary = SWEbenchAdapter(output, model_name="test-agent").run(
        load_instances(manifest), [sys.executable, str(helper)]
    )
    predictions = [json.loads(line) for line in (output / "predictions.jsonl").read_text(encoding="utf-8").splitlines()]

    assert summary["agent_success_rate"] == 1.0
    assert summary["non_empty_patch_rate"] == 1.0
    assert predictions[0]["instance_id"] == "local__repo-1"
    assert "+VALUE = 2" in predictions[0]["model_patch"]
    assert "swebench.harness.run_evaluation" in official_evaluation_command(output / "predictions.jsonl")


def test_swebench_adapter_does_not_count_stopped_runtime_as_success(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q")
    git(source, "config", "user.email", "benchmark@example.com")
    git(source, "config", "user.name", "Benchmark")
    (source / "service.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(source, "add", "service.py")
    git(source, "commit", "-qm", "base")
    commit = git(source, "rev-parse", "HEAD").stdout.strip()
    helper = tmp_path / "stop.py"
    helper.write_text(
        "import json\nfrom pathlib import Path\np=Path('.pico/runs/run-1')\np.mkdir(parents=True)\n(p/'report.json').write_text(json.dumps({'status':'stopped','stop_reason':'step_limit_reached'}))\n",
        encoding="utf-8",
    )
    instance = {"instance_id": "local__stopped-1", "repo_path": str(source), "base_commit": commit, "problem_statement": "Inspect only."}

    summary = SWEbenchAdapter(tmp_path / "output").run([instance], [sys.executable, str(helper)])

    assert summary["agent_success_rate"] == 0.0
    assert summary["runs"][0]["stop_reason"] == "step_limit_reached"


def test_swebench_adapter_records_timeout_and_continues(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q")
    git(source, "config", "user.email", "benchmark@example.com")
    git(source, "config", "user.name", "Benchmark")
    (source / "service.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(source, "add", "service.py")
    git(source, "commit", "-qm", "base")
    commit = git(source, "rev-parse", "HEAD").stdout.strip()
    helper = tmp_path / "slow.py"
    helper.write_text("import time\ntime.sleep(2)\n", encoding="utf-8")
    instances = [
        {
            "instance_id": "local__timeout-1",
            "repo_path": str(source),
            "base_commit": commit,
            "problem_statement": "Wait too long.",
        },
        {
            "instance_id": "local__timeout-2",
            "repo_path": str(source),
            "base_commit": commit,
            "problem_statement": "Wait too long again.",
        },
    ]

    summary = SWEbenchAdapter(tmp_path / "output").run(
        instances,
        [sys.executable, str(helper)],
        timeout=1,
    )

    assert len(summary["runs"]) == 2
    assert summary["agent_success_rate"] == 0.0
    assert all(row["exit_code"] == 124 for row in summary["runs"])
    assert all(row["stop_reason"] == "adapter_timeout" for row in summary["runs"])
    assert all("timed out" in row["stderr"] for row in summary["runs"])


def test_remote_cache_fetches_only_the_pinned_commit(monkeypatch, tmp_path):
    commands = []

    def fake_run(command, cwd, timeout=300, env=None):
        del cwd, timeout, env
        argv = [str(item) for item in command]
        commands.append(argv)
        if argv[:3] == ["git", "init", "--bare"]:
            (tmp_path / "cache" / "owner__repo.git").mkdir(parents=True)
        if argv[:3] == ["git", "cat-file", "-e"]:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="missing")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(swebench_module, "_run", fake_run)
    adapter = SWEbenchAdapter(tmp_path / "output", cache_dir=tmp_path / "cache")

    source = adapter._source(
        {
            "repo": "owner/repo",
            "base_commit": "abc123",
        }
    )

    assert source.endswith("owner__repo.git")
    assert not any("clone" in command for command in commands)
    fetch = next(command for command in commands if "fetch" in command)
    assert "--depth=1" in fetch
    assert "--filter=blob:none" in fetch
    assert fetch[-1] == "abc123"
