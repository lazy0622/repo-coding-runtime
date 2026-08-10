#!/usr/bin/env python3
"""Resolve a pinned SWE-bench selection into an adapter JSONL manifest."""

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path


def fetch_rows(dataset, split):
    query = urllib.parse.urlencode({"dataset": dataset, "config": "default", "split": split})
    url = f"https://datasets-server.huggingface.co/first-rows?{query}"
    with urllib.request.urlopen(url, timeout=60) as response:
        payload = json.load(response)
    return [item["row"] for item in payload.get("rows", [])]


def build_manifest(selection, rows):
    by_id = {str(row["instance_id"]): row for row in rows}
    missing = [instance_id for instance_id in selection["instance_ids"] if instance_id not in by_id]
    if missing:
        raise RuntimeError(f"dataset preview did not contain pinned IDs: {missing}")
    return [
        {
            "instance_id": instance_id,
            "repo": by_id[instance_id]["repo"],
            "base_commit": by_id[instance_id]["base_commit"],
            "problem_statement": by_id[instance_id]["problem_statement"],
        }
        for instance_id in selection["instance_ids"]
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", default="benchmarks/swebench/mini-v1-selection.json")
    parser.add_argument("--output", default="artifacts/swebench-mini-v1/instances.jsonl")
    args = parser.parse_args()
    selection = json.loads(Path(args.selection).read_text(encoding="utf-8"))
    manifest = build_manifest(selection, fetch_rows(selection["dataset"], selection["split"]))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in manifest), encoding="utf-8")
    print(f"wrote {len(manifest)} pinned instances to {output}")


if __name__ == "__main__":
    main()
