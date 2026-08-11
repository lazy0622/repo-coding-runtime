"""SWE-bench compatible patch-generation adapter.

The adapter deliberately separates agent execution from grading.  It creates a
clean checkout for each real repository issue, records the generated Git diff,
and emits the official predictions JSON consumed by SWE-bench's Docker harness.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path


class SWEbenchAdapterError(RuntimeError):
    pass


def load_instances(path):
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        rows = json.loads(text)
    if not isinstance(rows, list):
        raise SWEbenchAdapterError("instance manifest must be a JSON array or JSONL rows")
    required = {"instance_id", "base_commit", "problem_statement"}
    for row in rows:
        missing = required - set(row)
        if missing or not (row.get("repo_path") or row.get("repo")):
            raise SWEbenchAdapterError(f"invalid instance {row.get('instance_id', '<unknown>')}: missing {sorted(missing)} or repository")
    return rows


def _run(command, cwd, timeout=300, env=None):
    argv = [str(item) for item in command]
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        timeout_message = f"command timed out after {timeout} seconds"
        stderr = f"{stderr.rstrip()}\n{timeout_message}" if stderr.strip() else timeout_message
        return subprocess.CompletedProcess(argv, 124, stdout=stdout, stderr=stderr)


class SWEbenchAdapter:
    def __init__(self, output_dir, model_name="repo-coding-runtime", cache_dir=None):
        self.output_dir = Path(output_dir).resolve()
        self.model_name = str(model_name)
        self.cache_dir = Path(cache_dir or self.output_dir / "repo-cache").resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _source(self, instance):
        if instance.get("repo_path"):
            source = Path(instance["repo_path"]).resolve()
            if not source.exists():
                raise SWEbenchAdapterError(f"repo_path does not exist: {source}")
            return str(source)
        cache_name = str(instance["repo"]).replace("/", "__") + ".git"
        mirror = self.cache_dir / cache_name
        if mirror.exists():
            probe = _run(["git", "rev-parse", "--is-bare-repository"], mirror, timeout=30)
            if probe.returncode != 0 or probe.stdout.strip() != "true":
                shutil.rmtree(mirror)
        if not mirror.exists():
            init = _run(["git", "init", "--bare", "--quiet", str(mirror)], self.cache_dir, timeout=60)
            if init.returncode != 0:
                raise SWEbenchAdapterError(init.stderr.strip() or "git bare cache init failed")
            remote = _run(
                ["git", "remote", "add", "origin", f"https://github.com/{instance['repo']}.git"],
                mirror,
                timeout=30,
            )
            if remote.returncode != 0:
                raise SWEbenchAdapterError(remote.stderr.strip() or "git cache remote setup failed")
        commit = str(instance["base_commit"])
        present = _run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], mirror, timeout=30)
        if present.returncode != 0:
            errors = []
            for _ in range(3):
                fetch = _run(
                    [
                        "git",
                        "-c",
                        "http.version=HTTP/1.1",
                        "fetch",
                        "--quiet",
                        "--no-tags",
                        "--depth=1",
                        "--filter=blob:none",
                        "origin",
                        commit,
                    ],
                    mirror,
                    timeout=300,
                )
                if fetch.returncode == 0:
                    break
                errors.append(fetch.stderr.strip() or "git pinned commit fetch failed")
            else:
                raise SWEbenchAdapterError("; ".join(errors))
        return str(mirror)

    def prepare(self, instance):
        instance_dir = self.output_dir / "instances" / str(instance["instance_id"])
        workspace = instance_dir / "repo"
        if instance_dir.exists():
            shutil.rmtree(instance_dir)
        instance_dir.mkdir(parents=True)
        source = self._source(instance)
        if instance.get("repo_path"):
            clone = _run(
                ["git", "clone", "--quiet", "--no-hardlinks", source, str(workspace)],
                self.output_dir,
            )
            if clone.returncode != 0:
                raise SWEbenchAdapterError(clone.stderr.strip() or "git clone failed")
            checkout = _run(["git", "checkout", "--quiet", str(instance["base_commit"])], workspace)
            if checkout.returncode != 0:
                raise SWEbenchAdapterError(checkout.stderr.strip() or "git checkout failed")
        else:
            mirror = Path(source)
            _run(["git", "worktree", "prune"], mirror, timeout=60)
            errors = []
            for _ in range(3):
                if workspace.exists():
                    shutil.rmtree(workspace)
                checkout = _run(
                    [
                        "git",
                        "-c",
                        "http.version=HTTP/1.1",
                        "worktree",
                        "add",
                        "--detach",
                        "--force",
                        str(workspace),
                        str(instance["base_commit"]),
                    ],
                    mirror,
                    timeout=900,
                )
                if checkout.returncode == 0:
                    break
                errors.append(checkout.stderr.strip() or "git worktree checkout failed")
                _run(["git", "worktree", "prune"], mirror, timeout=60)
            else:
                raise SWEbenchAdapterError("; ".join(errors))
        prompt_path = instance_dir / "problem_statement.md"
        prompt_path.write_text(str(instance["problem_statement"]), encoding="utf-8")
        return instance_dir, workspace, prompt_path

    def run_instance(self, instance, agent_command, timeout=900):
        instance_dir, workspace, prompt_path = self.prepare(instance)
        command = [
            str(item)
            .replace("{workspace}", str(workspace))
            .replace("{prompt_file}", str(prompt_path))
            .replace("{instance_id}", str(instance["instance_id"]))
            .replace("{problem_statement}", str(instance["problem_statement"]))
            for item in agent_command
        ]
        env = os.environ.copy()
        env["REPO_TASK_PROMPT"] = str(instance["problem_statement"])
        env["REPO_TASK_PROMPT_FILE"] = str(prompt_path)
        started = time.monotonic()
        result = _run(command, workspace, timeout=timeout, env=env)
        duration = time.monotonic() - started
        diff = _run(["git", "diff", "--binary", "--no-ext-diff"], workspace, timeout=60)
        if diff.returncode != 0:
            raise SWEbenchAdapterError(diff.stderr.strip() or "git diff failed")
        patch_path = instance_dir / "model.patch"
        patch_path.write_text(diff.stdout, encoding="utf-8")
        runtime_reports = sorted(
            (workspace / ".pico" / "runs").glob("*/report.json"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        ) if (workspace / ".pico" / "runs").is_dir() else []
        runtime_status = ""
        stop_reason = ""
        if runtime_reports:
            try:
                report = json.loads(runtime_reports[0].read_text(encoding="utf-8"))
                runtime_status = str(report.get("status", ""))
                stop_reason = str(report.get("stop_reason", ""))
                execution_policy = dict(report.get("execution_policy", {}) or {})
            except (OSError, ValueError, TypeError):
                runtime_status = "unreadable_report"
                execution_policy = {}
        else:
            execution_policy = {}
        agent_completed = result.returncode == 0 and runtime_status not in {"stopped", "failed", "unreadable_report"}
        if result.returncode == 124 and not stop_reason:
            stop_reason = "adapter_timeout"
            runtime_status = "timed_out"
        run_record = {
            "instance_id": str(instance["instance_id"]),
            "command": command,
            "exit_code": result.returncode,
            "agent_completed": agent_completed,
            "runtime_status": runtime_status or "external_command_completed",
            "stop_reason": stop_reason,
            "duration_seconds": round(duration, 3),
            "patch_bytes": len(diff.stdout.encode("utf-8")),
            "tool_steps": int(report.get("tool_steps", 0)) if runtime_reports and runtime_status != "unreadable_report" else 0,
            "first_edit_step": int(execution_policy.get("first_edit_step", 0) or 0),
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-4000:],
            "workspace": str(workspace),
            "patch_path": str(patch_path),
        }
        (instance_dir / "run.json").write_text(json.dumps(run_record, ensure_ascii=False, indent=2), encoding="utf-8")
        prediction = {
            "instance_id": str(instance["instance_id"]),
            "model_patch": diff.stdout,
            "model_name_or_path": self.model_name,
        }
        return prediction, run_record

    def run(self, instances, agent_command, timeout=900):
        predictions = []
        runs = []
        for instance in instances:
            prediction, record = self.run_instance(instance, agent_command, timeout=timeout)
            predictions.append(prediction)
            runs.append(record)
        predictions_path = self.output_dir / "predictions.jsonl"
        predictions_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in predictions),
            encoding="utf-8",
        )
        summary = {
            "artifact_type": "swebench-generation-v1",
            "instance_count": len(instances),
            "agent_success_rate": sum(row["agent_completed"] for row in runs) / len(runs) if runs else 0.0,
            "non_empty_patch_rate": sum(row["patch_bytes"] > 0 for row in runs) / len(runs) if runs else 0.0,
            "predictions_path": str(predictions_path),
            "runs": runs,
        }
        (self.output_dir / "generation-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary


def official_evaluation_command(predictions_path, dataset_name="SWE-bench/SWE-bench_Lite", run_id="repo-runtime"):
    """Return the official Docker harness command without hiding its dependency."""

    return [
        "python",
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        str(dataset_name),
        "--predictions_path",
        str(Path(predictions_path).resolve()),
        "--run_id",
        str(run_id),
    ]
