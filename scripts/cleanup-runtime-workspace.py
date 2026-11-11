"""Preview or clean one terminal job; never remove accepted patch/log artifacts."""

import argparse
import json

from pico.service.config import Settings
from pico.service.maintenance import cleanup_job_workspaces
from pico.service.store import Store

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("job_id")
parser.add_argument(
    "--apply", action="store_true", help="Without this flag only preview"
)
args = parser.parse_args()
settings = Settings.load()
print(
    json.dumps(
        cleanup_job_workspaces(
            settings, Store(settings.database_url), args.job_id, apply=args.apply
        )
    )
)
