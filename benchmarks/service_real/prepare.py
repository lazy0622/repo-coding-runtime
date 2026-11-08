"""Freeze historical tasks before model runs; do not inject synthetic bugs."""

import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / "artifacts/service/real-tasks-v1"


def git(path, *args):
    return subprocess.check_output(["git", *args], cwd=path)


def main():
    FOLDER.mkdir(parents=True, exist_ok=True)
    manifest = FOLDER / "frozen.json"
    if manifest.exists():
        raise SystemExit("Already frozen; keep the existing cases and results")
    cases_bytes = Path(__file__).with_name("cases.json").read_bytes()
    cases = json.loads(cases_bytes)["cases"]
    repositories = json.loads(
        (ROOT / "artifacts/service/repositories.json").read_text()
    )
    frozen = []
    for case in cases:
        cache = (
            ROOT
            / "artifacts/service/source-cache"
            / ("humanize" if case["id"].startswith("humanize") else "itsdangerous")
        )
        base = git(cache, "rev-parse", case["fix_commit"] + "^").decode().strip()
        record = {**case, "upstream_base_commit": base}
        codes = {}
        for label, commit in (("base", base), ("reference", case["fix_commit"])):
            target = FOLDER / case["id"] / label
            target.mkdir(parents=True)
            archive = target.parent / (label + ".tar")
            archive.write_bytes(git(cache, "archive", commit))
            with tarfile.open(archive) as source:
                source.extractall(target, filter="data")
            # Upstream generates this ignored build metadata from Git tags.
            # Make imports possible without adding reference history or a build.
            if case["id"].startswith("humanize"):
                (target / "src/humanize/_version.py").write_text(
                    '__version__ = "0+evaluation"\n', encoding="utf-8"
                )
            for command in [
                ("init",),
                ("add", "-f", "."),
                (
                    "-c",
                    "user.name=Runtime Evaluation",
                    "-c",
                    "user.email=runtime@example.invalid",
                    "commit",
                    "-m",
                    "Snapshot upstream " + commit,
                ),
            ]:
                git(target, *command)
            record[label + "_snapshot_commit"] = (
                git(target, "rev-parse", "HEAD").decode().strip()
            )
            run = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("verify.py")),
                    case["id"],
                    str(target),
                ],
                cwd=target,
                capture_output=True,
                timeout=60,
            )
            (target.parent / (label + "-verification.log")).write_bytes(
                run.stdout + run.stderr
            )
            codes[label] = run.returncode
            if label == "base":
                regression = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "import sys;sys.path.insert(0,sys.argv.pop(1));import pytest;"
                        "raise SystemExit(pytest.main(sys.argv[1:]))",
                        str(target / "src"),
                        *case["regression_files"],
                        "-q",
                        "-o",
                        "addopts=",
                    ],
                    cwd=target,
                    capture_output=True,
                    timeout=60,
                )
                (target.parent / "base-upstream-regression.log").write_bytes(
                    regression.stdout + regression.stderr
                )
                record["base_upstream_regression_exit_code"] = regression.returncode
                if regression.returncode:
                    raise RuntimeError(
                        "Base upstream regressions failed: " + case["id"]
                    )
        record["preflight_exit_codes"] = codes
        if codes["base"] == 0 or codes["reference"] != 0:
            raise RuntimeError(
                "Invalid task preflight: " + case["id"] + " " + str(codes)
            )
        frozen.append(record)
        repositories[case["id"]] = {
            "path": str(FOLDER / case["id"] / "base"),
            "verify_command": [
                sys.executable,
                str(Path(__file__).with_name("verify.py")),
                case["id"],
                "{workspace}",
            ],
        }
        print(json.dumps({"case": case["id"], "preflight": codes}), flush=True)
    manifest.write_text(
        json.dumps(
            {
                "tier": "curated_historical_validation_not_unseen_benchmark",
                "cases_sha256": hashlib.sha256(cases_bytes).hexdigest(),
                "verifier_sha256": hashlib.sha256(
                    Path(__file__).with_name("verify.py").read_bytes()
                ).hexdigest(),
                "cases": frozen,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (ROOT / "artifacts/service/repositories.json").write_text(
        json.dumps(repositories, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
