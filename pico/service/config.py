"""Trusted server configuration; requests select repository IDs, never commands."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Repository:
    path: Path
    verify_command: list[str]
    agent_command: list[str] | None = None
    sandbox: str = "host"
    agent_verify_command: list[str] | None = None


@dataclass
class Settings:
    database_url: str
    api_token: str
    data_dir: Path
    repositories: dict[str, Repository]
    lease_seconds: float = 15
    task_timeout: float = 300
    verify_timeout: float = 60

    @classmethod
    def load(cls):
        load_dotenv(Path.cwd() / ".env.service", override=False)
        config_path = Path(os.environ["RUNTIME_REPOSITORIES_FILE"]).resolve(strict=True)
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        repositories = {}
        for key, value in raw.items():
            command = value["verify_command"]
            if (
                not isinstance(command, list)
                or not command
                or not all(isinstance(arg, str) for arg in command)
            ):
                raise ValueError("verify_command must be a nonempty argument list")
            development = value.get("agent_verify_command")
            if development is not None and (
                not isinstance(development, list)
                or not development
                or not all(isinstance(arg, str) for arg in development)
            ):
                raise ValueError(
                    "agent_verify_command must be a nonempty argument list"
                )
            sandbox = value.get("sandbox", "host")
            if sandbox not in {"host", "docker"}:
                raise ValueError("Invalid sandbox setting")
            repositories[key] = Repository(
                Path(value["path"]).resolve(strict=True),
                command,
                value.get("agent_command"),
                sandbox,
                development,
            )
        token = os.environ["RUNTIME_API_TOKEN"]
        if len(token) < 24:
            raise ValueError("Use a generated API token of at least 24 characters")
        settings = cls(
            os.environ["RUNTIME_DATABASE_URL"],
            token,
            Path(os.environ.get("RUNTIME_DATA_DIR", "artifacts/service")).resolve(),
            repositories,
            float(os.environ.get("RUNTIME_LEASE_SECONDS", "15")),
            float(os.environ.get("RUNTIME_TASK_TIMEOUT", "300")),
            float(os.environ.get("RUNTIME_VERIFY_TIMEOUT", "60")),
        )
        if (
            settings.lease_seconds < 2
            or min(settings.task_timeout, settings.verify_timeout) <= 0
        ):
            raise ValueError("Invalid lease or execution timeout")
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        return settings
