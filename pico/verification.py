"""Workspace verification for the V1.5 Plan–Execute–Verify loop."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

from .command_runner import run_shell_command
from .security import classify_shell_command, shell_env
from .workspace import now


VERIFY_PASSED = "passed"
VERIFY_FAILED = "failed"
VERIFY_SKIPPED = "skipped"
VERIFY_BLOCKED = "blocked"


@dataclass(frozen=True)
class VerificationResult:
    status: str
    command: str = ""
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    error_code: str = ""
    reason: str = ""
    risk_level: str = "low"
    created_at: str = ""

    @property
    def passed(self):
        return self.status == VERIFY_PASSED

    def to_dict(self):
        return {
            "status": self.status,
            "passed": self.passed,
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "error_code": self.error_code,
            "reason": self.reason,
            "risk_level": self.risk_level,
            "created_at": self.created_at,
        }


def run_verification(root, command, timeout=60, env=None):
    """Run an explicitly configured verifier in the workspace.

    The command is never inferred from arbitrary model text.  It must be
    supplied by the caller, and destructive commands are blocked by the same
    lightweight policy used for shell tools.
    """

    command = str(command or "").strip()
    if not command:
        return VerificationResult(status=VERIFY_SKIPPED, reason="no_verify_command", created_at=now())

    policy = classify_shell_command(command)
    if policy["decision"] == "deny":
        return VerificationResult(
            status=VERIFY_BLOCKED,
            command=command,
            error_code="verification_command_blocked",
            reason=policy["reason"],
            risk_level=policy["risk_level"],
            created_at=now(),
        )

    started_at = time.monotonic()
    try:
        result = run_shell_command(
            command,
            cwd=root,
            timeout=max(1, int(timeout)),
            env=env or shell_env(root=root),
        )
        return VerificationResult(
            status=VERIFY_PASSED if result.returncode == 0 else VERIFY_FAILED,
            command=command,
            exit_code=int(result.returncode),
            stdout=str(result.stdout or ""),
            stderr=str(result.stderr or ""),
            duration_ms=int((time.monotonic() - started_at) * 1000),
            error_code="" if result.returncode == 0 else "verification_failed",
            reason="exit_code_zero" if result.returncode == 0 else "non_zero_exit_code",
            risk_level=policy["risk_level"],
            created_at=now(),
        )
    except subprocess.TimeoutExpired as exc:
        return VerificationResult(
            status=VERIFY_FAILED,
            command=command,
            stdout=str(exc.stdout or ""),
            stderr=str(exc.stderr or ""),
            duration_ms=int((time.monotonic() - started_at) * 1000),
            error_code="verification_timeout",
            reason="timeout",
            risk_level=policy["risk_level"],
            created_at=now(),
        )
    except Exception as exc:
        return VerificationResult(
            status=VERIFY_FAILED,
            command=command,
            duration_ms=int((time.monotonic() - started_at) * 1000),
            error_code="verification_error",
            reason=f"{exc.__class__.__name__}: {exc}",
            risk_level=policy["risk_level"],
            created_at=now(),
        )
