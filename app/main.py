"""Main entry point for the Job Opportunity Scanner service."""

import argparse
import sys
from pathlib import Path


def main() -> int:
    """
    Main entry point for the Job Opportunity Scanner.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    parser = argparse.ArgumentParser(
        description="Job Opportunity Scanner - Automated job posting monitoring and notification service"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Path to configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--manual-run",
        action="store_true",
        help="Run a single scan immediately and exit (useful for testing)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Log level (default: INFO)",
    )

    args = parser.parse_args()

    try:
        # TODO: Implement service initialization and startup logic
        # 1. Load configuration from args.config
        # 2. Initialize logging with args.log_level
        # 3. Initialize database connection and run migrations
        # 4. Initialize scheduler
        # 5. If --manual-run, execute pipeline once; otherwise start scheduler
        print(f"Job Opportunity Scanner initialized with config: {args.config}")
        print(f"Log level: {args.log_level}")
        if args.manual_run:
            print("Running manual scan (--manual-run mode)...")
        else:
            print("Starting scheduler...")
        return 0
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
