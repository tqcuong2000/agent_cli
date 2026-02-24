import argparse
import os
import sys

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

    print("Hello from agent_cli!")
    print(f"Root Folder: {args.root}")
    
    if args.verbose:
        print("Verbosity is ON")
    
    print(f"Other Arguments: {sys.argv[1:]}")

if __name__ == "__main__":
    main()
