#!/usr/bin/env python3
"""Run the provider-free native Tool Calling compatibility benchmark."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pico.evaluation.tool_protocol import render_tool_protocol_markdown, run_tool_protocol_benchmark


def main():
    output_dir = ROOT / "artifacts" / "tool-protocol-v1"
    artifact = run_tool_protocol_benchmark(output_dir / "protocol.json")
    (output_dir / "protocol.md").write_text(render_tool_protocol_markdown(artifact), encoding="utf-8")
    print(json.dumps(artifact["summary"] | artifact["metrics"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if artifact["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
