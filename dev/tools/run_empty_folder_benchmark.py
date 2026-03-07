from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from agent_cli.core.runtime.benchmark import (
    list_benchmark_scenarios,
    run_benchmark_scenario,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a reproducible benchmark scenario."
    )
    available_scenarios = list_benchmark_scenarios()
    parser.add_argument(
        "--output",
        help="Output directory for logs, sessions, and export artifacts.",
    )
    parser.add_argument(
        "--workspace",
        help="Optional empty workspace directory. If omitted, one is created under the output directory.",
    )
    parser.add_argument(
        "--model",
        help="Optional model override for the benchmark run.",
    )
    parser.add_argument(
        "--agent",
        help="Optional default agent override for the benchmark run.",
    )
    parser.add_argument(
        "--scenario",
        choices=available_scenarios,
        default="empty_folder_communication_v1",
        help="Benchmark scenario identifier to run.",
    )
    args = parser.parse_args()

    output_dir = (
        Path(args.output)
        if args.output
        else Path.cwd()
        / "tmp"
        / "benchmarks"
        / datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    )

    payload = asyncio.run(
        run_benchmark_scenario(
            output_dir=output_dir,
            workspace_dir=args.workspace,
            model=args.model,
            agent_name=args.agent,
            scenario=args.scenario,
        )
    )
    export_path = output_dir / "benchmark_run.json"
    report_path = output_dir / "benchmark_report.md"
    print(
        json.dumps(
            {
                "scenario": args.scenario,
                "export_json": str(export_path),
                "report_md": str(report_path),
            },
            indent=2,
        )
    )
    print(
        f"Tokens={payload.get('aggregate', {}).get('total_tokens', 0)} "
        f"CostUSD={payload.get('aggregate', {}).get('total_cost_usd', 0.0)} "
        f"Compliance={payload.get('aggregate', {}).get('average_compliance_score', 0.0)}"
    )


if __name__ == "__main__":
    main()
