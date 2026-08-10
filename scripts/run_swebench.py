"""Generate SWE-bench predictions from real repository checkouts."""

import argparse
import json

from pico.evaluation.swebench import SWEbenchAdapter, load_instances, official_evaluation_command


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--agent-command-json", required=True, help='JSON argv, e.g. ["repo","--prompt-file","{prompt_file}"]')
    parser.add_argument("--model-name", default="repo-coding-runtime")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--dataset", default="SWE-bench/SWE-bench_Lite")
    args = parser.parse_args()
    command = json.loads(args.agent_command_json)
    if not isinstance(command, list) or not command:
        parser.error("--agent-command-json must be a non-empty JSON array")
    adapter = SWEbenchAdapter(args.output, model_name=args.model_name)
    summary = adapter.run(load_instances(args.manifest), command, timeout=args.timeout)
    summary["official_evaluation_command"] = official_evaluation_command(summary["predictions_path"], args.dataset)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
