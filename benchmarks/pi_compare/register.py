"""Register Pi-only aliases for the frozen cases; retain all original executors."""

import argparse
import json
import os
import sys
from pathlib import Path

from pico.service.config import Settings


def main(args):
    Settings.load()
    path = Path(os.environ["RUNTIME_REPOSITORIES_FILE"])
    registry = json.loads(path.read_text(encoding="utf-8"))
    frozen = json.loads(Path("artifacts/service/real-tasks-v1/frozen.json").read_text())
    for case in frozen["cases"]:
        name = case["id"] + "--pi"
        row = dict(registry[case["id"]])
        row.pop("agent_verify_command", None)
        row["agent_command"] = [
            sys.executable,
            "-X",
            "utf8",
            "-m",
            "benchmarks.pi_compare.pi_agent",
            "--node",
            str(args.node.resolve(strict=True)),
            "--cli",
            str(args.cli.resolve(strict=True)),
            "--shell",
            str(args.shell.resolve(strict=True)),
            "--workspace",
            "{workspace}",
            "--",
            "{task}",
        ]
        if name in registry and registry[name] != row:
            raise ValueError("Pi alias already has different trusted configuration")
        registry[name] = row
    temporary = path.with_suffix(".pi-registration.tmp")
    temporary.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    temporary.replace(path)
    print("Registered five Pi aliases; restart idle API/workers to load the registry")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("node", "cli", "shell"):
        parser.add_argument("--" + name, type=Path, required=True)
    main(parser.parse_args())
