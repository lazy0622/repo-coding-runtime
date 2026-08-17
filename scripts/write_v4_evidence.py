#!/usr/bin/env python3
"""Write a path-normalized V4 release evidence summary."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pico.evaluation.v4_evidence import write_v4_evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write claim-safe Repo Coding Runtime V4 evidence.")
    parser.add_argument(
        "--output",
        default="benchmarks/reporuntimebench/results/v4-evidence-summary.json",
        help="Versioned JSON evidence path.",
    )
    parser.add_argument("--markdown-output", default=None, help="Optional Markdown evidence path.")
    parser.add_argument("--verification-status", default="not_run", choices=("not_run", "passed", "partial", "failed"))
    parser.add_argument("--verification-command", action="append", default=[])
    args = parser.parse_args(argv)
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    markdown_path = Path(args.markdown_output) if args.markdown_output else None
    if markdown_path is not None and not markdown_path.is_absolute():
        markdown_path = ROOT / markdown_path
    evidence = write_v4_evidence(
        ROOT,
        output_path,
        markdown_path=markdown_path,
        verification={"status": args.verification_status, "commands": args.verification_command},
    )
    try:
        output_label = output_path.relative_to(ROOT).as_posix()
    except ValueError:
        output_label = str(output_path)
    print(
        json.dumps(
            {
                "output": output_label,
                "schema_version": evidence["schema_version"],
                "git_revision": evidence.get("git_revision"),
                "local_verification": evidence["local_verification"],
                "official_grade_status": evidence["official_evaluation"]["v4"]["status"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
