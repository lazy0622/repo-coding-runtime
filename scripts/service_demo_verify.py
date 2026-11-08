"""Server-owned behavioral checks, kept outside the agent's worktree."""

import runpy
import sys
import time
from pathlib import Path

workspace = Path(sys.argv[1]).resolve(strict=True)
if len(sys.argv) > 2:
    time.sleep(float(sys.argv[2]))
add = runpy.run_path(str(workspace / "calculator.py"))["add"]
for a, b in [(2, 3), (-2, 3), (0, 0), (12, -4), (1.25, 2.5)]:
    assert add(a, b) == a + b, (a, b, add(a, b))
print("5 server-owned behavioral cases passed")
