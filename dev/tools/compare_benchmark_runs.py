from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_cli.core.runtime.benchmark import compare_benchmark_runs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare two exported benchmark runs."
    )
    parser.add_argument("baseline", help="Path to baseline benchmark_run.json")
    parser.add_argument("candidate", help="Path to candidate benchmark_run.json")
    parser.add_argument(
        "--output",
        help="Optional path to write the comparison JSON.",
    )
    args = parser.parse_args()

    comparison = compare_benchmark_runs(args.baseline, args.candidate)
    serialized = json.dumps(comparison, ensure_ascii=True, indent=2)
    if args.output:
        Path(args.output).write_text(serialized, encoding="utf-8")
    print(serialized)


if __name__ == "__main__":
    main()
