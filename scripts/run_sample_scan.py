#!/usr/bin/env python3
"""Sample scan harness for end-to-end validation.

This script provides a manual way to validate the Job Opportunity Scanner
pipeline without running pytest. It can operate in two modes:

1. Fixture mode (default): Uses deterministic fixture data from YAML files
2. Real endpoint mode: Connects to live ATS APIs (requires network access)

Usage:
    # Run with fixtures (no network required)
    python scripts/run_sample_scan.py --config docs/sample_end_validation.yaml

    # Run with real endpoints (requires network and valid config)
    END_VALIDATION_REAL_RUN=1 python scripts/run_sample_scan.py --config config.yaml

    # Custom database path
    python scripts/run_sample_scan.py --config docs/sample_end_validation.yaml --database /tmp/test.db

    # Custom fixtures directory
    python scripts/run_sample_scan.py --config docs/sample_end_validation.yaml --fixtures tests/fixtures/end_validation
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from app.config.loader import load_config
from app.logging.config import configure_logging
from app.matching.engine import KeywordMatcher
from app.notifications.service import NotificationService
from app.persistence.database import close_database, init_database
from app.pipeline import ScanPipeline
from tests.helpers.fixture_adapter import FixtureAdapter


def print_header(title: str):
    """Print a formatted section header."""
    width = 80
    print("\n" + "=" * width)
    print(f" {title}")
    print("=" * width + "\n")


def print_summary_table(result):
    """Print a formatted summary table of pipeline results."""
    print_header("Pipeline Execution Summary")

    # Metrics table
    metrics = [
        ("Total Jobs Fetched", result.total_fetched),
        ("Total Jobs Normalized", result.total_normalized),
        ("Total Jobs Persisted", result.total_upserted),
        ("Total Jobs Matched", result.total_matched),
        ("Total Notifications Sent", result.total_notified),
        ("Total Errors", result.total_errors),
        ("Had Errors", "Yes" if result.had_errors else "No"),
        ("Duration (seconds)", f"{result.total_duration_seconds:.2f}"),
    ]

    # Calculate column widths
    max_label_width = max(len(label) for label, _ in metrics)

    print("┌" + "─" * (max_label_width + 2) + "┬" + "─" * 22 + "┐")
    print(f"│ {'Metric':<{max_label_width}} │ {'Value':<20} │")
    print("├" + "─" * (max_label_width + 2) + "┼" + "─" * 22 + "┤")

    for label, value in metrics:
        print(f"│ {label:<{max_label_width}} │ {str(value):<20} │")

    print("└" + "─" * (max_label_width + 2) + "┴" + "─" * 22 + "┘")

    # Per-source breakdown
    if result.source_stats:
        print("\n" + "-" * 80)
        print(" Per-Source Breakdown")
        print("-" * 80 + "\n")

        for stats in result.source_stats:
            print(f"Source: {stats.source_id}")
            print(f"  Fetched: {stats.fetched_count}")
            print(f"  Normalized: {stats.normalized_count}")
            print(f"  Persisted: {stats.upserted_count}")
            print(f"  Matched: {stats.matched_count}")
            print(f"  Notified: {stats.notified_count}")
            print(f"  Errors: {stats.error_count}")
            if stats.error_message:
                print(f"  Error Message: {stats.error_message}")
            print()


def main():
    """Main entry point for sample scan harness."""
    parser = argparse.ArgumentParser(
        description="Run a sample scan for end-to-end validation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("docs/sample_end_validation.yaml"),
        help="Path to configuration file (default: docs/sample_end_validation.yaml)",
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=Path("tests/fixtures/end_validation/sample_jobs.yaml"),
        help="Path to fixtures YAML file (default: tests/fixtures/end_validation/sample_jobs.yaml)",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/sample_end_validation.db"),
        help="Path to SQLite database (default: data/sample_end_validation.db)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Log level (default: INFO)",
    )

    args = parser.parse_args()

    # Load environment variables
    load_dotenv()

    # Check for real endpoint mode
    use_real_endpoints = os.environ.get("END_VALIDATION_REAL_RUN", "0") == "1"

    print_header("Job Opportunity Scanner - Sample Scan Harness")

    print(f"Configuration file: {args.config}")
    print(f"Database: {args.database}")
    print(f"Log level: {args.log_level}")

    if use_real_endpoints:
        print(f"\n⚠️  REAL ENDPOINT MODE ENABLED")
        print(f"   The scanner will make actual HTTP requests to ATS APIs.")
        print(f"   This may be rate-limited or consume API quota.")
        response = input("\nContinue? [y/N]: ")
        if response.lower() != "y":
            print("Aborted.")
            return 1
    else:
        print(f"Fixture mode: {args.fixtures}")
        print(f"\nUsing fixture data (no network requests will be made)")

    # Validate config file exists
    if not args.config.exists():
        print(f"\n❌ Error: Configuration file not found: {args.config}")
        return 1

    # Validate fixtures exist if in fixture mode
    if not use_real_endpoints and not args.fixtures.exists():
        print(f"\n❌ Error: Fixture file not found: {args.fixtures}")
        print(f"   Run with END_VALIDATION_REAL_RUN=1 to use real endpoints instead.")
        return 1

    try:
        # Load configuration
        print("\n📋 Loading configuration...")
        app_config, env_config = load_config(args.config)

        # Override database URL with our custom path
        database_url = f"sqlite:///{args.database.absolute()}"
        env_config.database_url = database_url

        # Override log level
        env_config.log_level = args.log_level

        # Configure logging
        configure_logging(
            level=args.log_level,
            format_type=app_config.logging.format if app_config.logging else "key-value",
            environment="validation",
        )

        print(f"✓ Loaded {len(app_config.sources)} sources")
        enabled_sources = [s for s in app_config.sources if s.enabled]
        print(f"✓ {len(enabled_sources)} sources enabled")

        # Initialize database
        print(f"\n💾 Initializing database: {args.database}")
        init_database(database_url)
        print(f"✓ Database initialized")

        # Create services
        print("\n🔧 Initializing services...")
        notification_service = NotificationService()
        keyword_matcher = KeywordMatcher(app_config.search_criteria)
        print("✓ Services initialized")

        # Create pipeline
        pipeline = ScanPipeline(
            app_config=app_config,
            env_config=env_config,
            notification_service=notification_service,
            keyword_matcher=keyword_matcher,
        )

        # Execute scan
        print("\n🚀 Executing pipeline scan...")
        print(f"   Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        if use_real_endpoints:
            # Real endpoint mode - no patching
            result = pipeline.run_once()
        else:
            # Fixture mode - patch the adapter factory
            with patch("app.pipeline.runner.get_adapter") as mock_get_adapter:
                # Create fixture adapter
                fixture_adapter = FixtureAdapter(args.fixtures)
                mock_get_adapter.return_value = fixture_adapter

                # Also mock SMTP to avoid sending real emails
                with patch("app.notifications.smtp_client.SMTPClient.send") as mock_send:
                    mock_send.return_value = None
                    result = pipeline.run_once()

        print(f"   Completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # Print results
        print_summary_table(result)

        # Print database and log locations
        print_header("Output Locations")
        print(f"Database: {args.database.absolute()}")
        print(f"  View with: sqlite3 {args.database.absolute()}")
        print(f"\nTo inspect the database:")
        print(f"  sqlite3 {args.database.absolute()} '.tables'")
        print(f"  sqlite3 {args.database.absolute()} 'SELECT * FROM jobs;'")
        print(f"  sqlite3 {args.database.absolute()} 'SELECT * FROM alerts_sent;'")

        # Cleanup instructions
        print("\n" + "-" * 80)
        print(f"To clean up: rm {args.database.absolute()}")
        print("-" * 80 + "\n")

        # Close database
        close_database()

        # Exit with appropriate code
        return 1 if result.had_errors else 0

    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
