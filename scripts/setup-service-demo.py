"""Create a dedicated buggy Git fixture and trusted service repository config."""

import json
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
folder = root / "artifacts/service"
config = folder / "repositories.json"
if config.exists():
    raise SystemExit("Repository configuration already exists; left unchanged")
source = folder / "demo-repository"
source.mkdir(parents=True, exist_ok=True)
if not (source / ".git").exists():
    (source / "calculator.py").write_text(
        "def add(a, b):\n    return a - b\n", encoding="utf-8"
    )
    (source / ".gitignore").write_text(".pico/\n__pycache__/\n", encoding="utf-8")
    commands = [
        ["init"],
        ["add", "."],
        [
            "-c",
            "user.name=Runtime Demo",
            "-c",
            "user.email=runtime@example.invalid",
            "commit",
            "-m",
            "Add intentionally broken demo",
        ],
    ]
    for command in commands:
        subprocess.run(["git", *command], cwd=source, check=True, capture_output=True)
verify = [sys.executable, str(root / "scripts/service_demo_verify.py"), "{workspace}"]
agent = [sys.executable, str(root / "scripts/service_demo_agent.py"), "{workspace}"]
repositories = {
    name: {
        "path": str(source),
        "verify_command": verify + (["20"] if name == "demo-recovery" else []),
        **({"agent_command": agent} if name != "demo-agent" else {}),
    }
    for name in ["demo-fixture", "demo-agent", "demo-recovery"]
}
config.write_text(json.dumps(repositories, indent=2), encoding="utf-8")
print("Created labeled fixture and real-model demo configurations")
