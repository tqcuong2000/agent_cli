from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from agent_cli.core.runtime.benchmark import run_benchmark_scenario


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the file-operations benchmark scenario."
    )
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
            scenario="file_operations_tool_flow_v1",
        )
    )
    export_path = output_dir / "benchmark_run.json"
    report_path = output_dir / "benchmark_report.md"
    print(
        json.dumps(
            {
                "scenario": "file_operations_tool_flow_v1",
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
