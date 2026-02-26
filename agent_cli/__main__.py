import argparse
import os
import sys
from agent_cli.ux.tui.app import AgentCLIApp

def main():
    parser = argparse.ArgumentParser(
        description="agent_cli: A basic python module project template"
    )
    
    # Example argument: Verbose mode
    parser.add_argument(
        "-v", "--verbose", 
        action="store_true", 
        help="Increase output verbosity"
    )
    
    # Dynamic root folder
    parser.add_argument(
        "--root", 
        default=os.getcwd(),
        help="Root folder for operations (defaults to current directory)"
    )

    args = parser.parse_args()

    # Create and run the Textual application
    app = AgentCLIApp(root_folder=args.root)
    
    # Only run the app when not running tests
    if "pytest" not in sys.modules:
        app.run()

if __name__ == "__main__":
    main()
