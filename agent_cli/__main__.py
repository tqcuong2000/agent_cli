import argparse
import asyncio
import logging
import os
import sys

from agent_cli.ux.tui.app import AgentCLIApp

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="agent_cli: A basic python module project template"
    )

    # Example argument: Verbose mode
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Increase output verbosity"
    )

    # Dynamic root folder
    parser.add_argument(
        "--root",
        default=os.getcwd(),
        help="Root folder for operations (defaults to current directory)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Start with DEBUG log level",
    )

    args = parser.parse_args()
    if args.debug:
        os.environ["AGENT_LOG_LEVEL"] = "DEBUG"

    # Create and run the Textual application
    app = AgentCLIApp(root_folder=args.root)

    # Only run the app when not running tests
    if "pytest" not in sys.modules:
        try:
            app.run()
        except KeyboardInterrupt:
            logger.warning("Interrupted by user (Ctrl+C).")
        finally:
            if app.app_context.is_running:
                asyncio.run(app.app_context.shutdown())


if __name__ == "__main__":
    main()
