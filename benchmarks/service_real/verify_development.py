"""Public development checks; independent acceptance behaviors stay separate."""

import sys
from pathlib import Path

workspace = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(workspace / "src"))
if sys.argv[1].startswith("humanize-"):
    files = (
        ["tests/test_filesize.py"]
        if sys.argv[1].endswith("rollover")
        else ["tests/test_number.py"]
    )
elif sys.argv[1] == "itsdangerous-future-timestamp":
    files = [
        "tests/test_itsdangerous/test_timed.py",
        "tests/test_itsdangerous/test_signer.py",
    ]
else:
    files = ["tests/test_itsdangerous/test_signer.py"]
import pytest

raise SystemExit(pytest.main([*files, "-q", "-o", "addopts="]))
