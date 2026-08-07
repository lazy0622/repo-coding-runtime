"""Small cross-platform helpers for durable atomic file replacement."""

from __future__ import annotations

import os
import time
from pathlib import Path


def replace_with_retry(source, target, *, retries=7, delay_seconds=0.03):
    """Replace *target* atomically, tolerating short Windows antivirus locks."""

    source = Path(source)
    target = Path(target)
    attempts = max(1, int(retries))
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return target
        except PermissionError as exc:
            winerror = getattr(exc, "winerror", None)
            if os.name != "nt" or winerror not in {5, 32} or attempt + 1 >= attempts:
                raise
            time.sleep(delay_seconds * (2**attempt))
    return target
