"""Main entry point for the Job Opportunity Scanner service."""

from dotenv import load_dotenv
load_dotenv()

import argparse
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

from app.config.duration import parse_duration, validate_duration_range
from app.config.environment import EnvironmentConfig
from app.config.exceptions import ConfigurationError
from app.config.loader import load_config
from app.config.models import AppConfig
from app.logging import get_logger
from app.logging.config import configure_logging
from app.logging.context import log_context
from app.matching.engine import KeywordMatcher
from app.notifications.service import NotificationService
from app.domain.models import DismissalScope
from app.persistence.database import close_database, get_session, init_database
from app.persistence.repositories import DismissedItemRepository
from app.pipeline import ScanPipeline
from app.scheduler import SchedulerService

logger = get_logger(__name__, component="cli")


def load_runtime_config(
    config_path: Path, log_level_override: str
) -> Tuple[AppConfig, EnvironmentConfig]:
    """
    Load and prepare runtime configuration.

    Args:
        config_path: Path to configuration file
        log_level_override: Log level from CLI (takes precedence)

    Returns:
        Tuple of (AppConfig, EnvironmentConfig) with scan_interval_seconds computed

    Raises:
        ConfigurationError: If configuration is invalid
    """
    # Load configurations
    app_config, env_config = load_config(config_path)

    # Parse and validate scan interval
    try:
        scan_interval_seconds = parse_duration(app_config.scan_interval)
        validate_duration_range(scan_interval_seconds)
        # Store computed value for easy access
        app_config.scan_interval_seconds = scan_interval_seconds
    except Exception as e:
        raise ConfigurationError(
            f"Invalid scan_interval: {e}",
            suggestions=[
                "Use ISO-8601 format (e.g., PT15M) or human-readable (e.g., 15m)",
                "Ensure interval is between 1 minute and 24 hours",
            ],
        )

    # Apply log level priority: CLI > Environment > Config
    if log_level_override:
        env_config.log_level = log_level_override
    elif env_config.log_level:
        # Environment variable already set
        pass
    elif app_config.logging and app_config.logging.level:
        env_config.log_level = app_config.logging.level
    else:
        env_config.log_level = "INFO"

    return app_config, env_config


def parse_dismissal_target(target: str) -> Tuple[DismissalScope, str]:
    """
    Parse a dismissal target into (scope, key).

    Accepts "<job_key>" (defaults to job scope) or "<scope>:<key>" where scope
    is one of job, company, or source. Only the first colon separates the scope,
    so keys may themselves contain colons.

    Raises:
        ValueError: If the scope prefix is not valid or the key is empty.
    """
    target = target.strip()
    if ":" in target:
        prefix, key = target.split(":", 1)
        try:
            scope = DismissalScope(prefix.strip().lower())
        except ValueError:
            valid = ", ".join(s.value for s in DismissalScope)
            raise ValueError(f"Invalid scope '{prefix}' (expected one of: {valid})") from None
    else:
        scope, key = DismissalScope.JOB, target

    key = key.strip()
    if not key:
        raise ValueError("Dismissal target must not be empty")
    return scope, key


def handle_dismissal_commands(args: argparse.Namespace) -> Optional[int]:
    """
    Run dismissal management commands if any dismissal flag was given.

    Resolves DATABASE_URL directly so no SMTP credentials or config file are
    required.

    Returns:
        Exit code if a dismissal command was handled, otherwise None.
    """
    if not (args.dismiss or args.undismiss or args.list_dismissed):
        return None

    if args.dismiss_reason and not args.dismiss:
        print("Error: --dismiss-reason requires --dismiss", file=sys.stderr)
        return 1

    try:
        dismiss_target = parse_dismissal_target(args.dismiss) if args.dismiss else None
        undismiss_target = parse_dismissal_target(args.undismiss) if args.undismiss else None
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    database_url = os.getenv("DATABASE_URL", "sqlite:///./data/job_scanner.db")
    try:
        init_database(database_url)
        with get_session() as session:
            repo = DismissedItemRepository(session)

            if dismiss_target:
                scope, key = dismiss_target
                item = repo.dismiss(key, scope=scope, reason=args.dismiss_reason)
                print(f"Dismissed {item.scope.value}: {item.item_key}")

            if undismiss_target:
                scope, key = undismiss_target
                if repo.undismiss(key, scope=scope):
                    print(f"Removed dismissal {scope.value}: {key.lower()}")
                else:
                    print(f"Dismissal not found: {scope.value}: {key.lower()}")

            if args.list_dismissed:
                items = repo.list_all()
                if not items:
                    print("No dismissed items.")
                else:
                    rows = [
                        (
                            i.scope.value,
                            i.item_key,
                            i.dismissed_at.strftime("%Y-%m-%d %H:%M"),
                            i.reason or "",
                        )
                        for i in items
                    ]
                    headers = ("SCOPE", "KEY", "DISMISSED (UTC)", "REASON")
                    widths = [
                        max(len(headers[c]), *(len(r[c]) for r in rows)) for c in range(4)
                    ]
                    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
                    print(fmt.format(*headers).rstrip())
                    print(fmt.format(*("-" * w for w in widths)).rstrip())
                    for row in rows:
                        print(fmt.format(*row).rstrip())
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        close_database()


def main() -> int:
    """
    Main entry point for the Job Opportunity Scanner.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    start_time = time.time()
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
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Log level (overrides config and environment)",
    )
    parser.add_argument(
        "--dismiss",
        metavar="TARGET",
        default=None,
        help="Dismiss a job, company, or source: <job_key>, job:<key>, company:<name>, or source:<id>",
    )
    parser.add_argument(
        "--dismiss-reason",
        metavar="TEXT",
        default=None,
        help="Optional reason to store with --dismiss",
    )
    parser.add_argument(
        "--undismiss",
        metavar="TARGET",
        default=None,
        help="Remove a dismissal (same TARGET format as --dismiss)",
    )
    parser.add_argument(
        "--list-dismissed",
        action="store_true",
        help="List all active dismissals and exit",
    )

    args = parser.parse_args()

    # Dismissal management runs standalone: no config, SMTP, or scan required
    dismissal_exit_code = handle_dismissal_commands(args)
    if dismissal_exit_code is not None:
        return dismissal_exit_code

    try:
        # Step 1: Load configuration early (before logging for format detection)
        app_config, env_config = load_runtime_config(args.config, args.log_level)

        # Step 2: Configure logging early
        log_format = (
            app_config.logging.format if app_config.logging else "key-value"
        )
        environment = os.environ.get("ENVIRONMENT", "local")
        configure_logging(level=env_config.log_level, format_type=log_format, environment=environment)

        logger.info(
            "Job Opportunity Scanner starting",
            extra={
                "event": "service.starting",
                "config_path": str(args.config),
                "log_level": env_config.log_level,
                "manual_run": args.manual_run,
            },
        )

        # Step 3: Initialize database
        init_database(env_config.database_url)

        # Log config loaded event
        enabled_sources = [s for s in app_config.sources if s.enabled]
        logger.info(
            "Configuration loaded",
            extra={
                "event": "config.loaded",
                "source_count": len(app_config.sources),
                "enabled_source_count": len(enabled_sources),
                "scan_interval_seconds": app_config.scan_interval_seconds,
                "log_format": log_format,
            },
        )

        # Step 4: Instantiate shared service singletons
        notification_service = NotificationService()
        keyword_matcher = KeywordMatcher(app_config.search_criteria)

        logger.info(
            "Services initialized",
            extra={
                "event": "services.initialized",
            },
        )

        # Step 5: Build pipeline
        pipeline = ScanPipeline(
            app_config=app_config,
            env_config=env_config,
            notification_service=notification_service,
            keyword_matcher=keyword_matcher,
        )

        # Step 6: Branch based on mode
        if args.manual_run:
            # Manual run: execute once and exit
            logger.info(
                "Executing manual scan",
                extra={"event": "service.manual_scan.starting"}
            )
            result = pipeline.run_once()

            # Log summary
            logger.info(
                f"Manual scan completed: "
                f"{result.total_fetched} fetched, "
                f"{result.total_normalized} normalized, "
                f"{result.total_upserted} persisted, "
                f"{result.total_matched} matched, "
                f"{result.total_notified} notified",
                extra={
                    "event": "service.manual_scan.completed",
                    "duration_seconds": result.total_duration_seconds,
                    "had_errors": result.had_errors,
                    "total_fetched": result.total_fetched,
                    "total_normalized": result.total_normalized,
                    "total_upserted": result.total_upserted,
                    "total_matched": result.total_matched,
                    "total_notified": result.total_notified,
                },
            )

            # Close database
            close_database()

            # Log service stopping
            uptime_seconds = time.time() - start_time
            logger.info(
                "Job Opportunity Scanner stopped",
                extra={
                    "event": "service.stopping",
                    "uptime_seconds": round(uptime_seconds, 2),
                },
            )

            # Exit with error code if there were errors
            return 1 if result.had_errors else 0

        else:
            # Daemon mode: start scheduler
            shutdown_event = threading.Event()

            # Create scheduler
            scheduler_service = SchedulerService(
                pipeline_callable=pipeline.run_once,
                interval_seconds=app_config.scan_interval_seconds,
                shutdown_event=shutdown_event,
            )

            # Set up signal handlers for graceful shutdown
            def signal_handler(signum, frame):
                logger.info(
                    f"Received signal {signum}, shutting down",
                    extra={"event": "service.signal_received", "signal": signum}
                )
                scheduler_service.shutdown(wait=False)
                close_database()

            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)

            # Start scheduler
            scheduler_service.start()

            logger.info(
                "Scheduler started. Press Ctrl+C to stop",
                extra={"event": "service.daemon_mode.started"}
            )

            # Block until shutdown event is set
            try:
                shutdown_event.wait()
            except KeyboardInterrupt:
                logger.info(
                    "Keyboard interrupt received, shutting down",
                    extra={"event": "service.keyboard_interrupt"}
                )
                scheduler_service.shutdown(wait=False)
                close_database()

            uptime_seconds = time.time() - start_time
            logger.info(
                "Job Opportunity Scanner stopped",
                extra={
                    "event": "service.stopping",
                    "uptime_seconds": round(uptime_seconds, 2),
                }
            )
            return 0

    except ConfigurationError as e:
        # Configuration errors are already formatted nicely
        print(f"Configuration Error: {e}", file=sys.stderr)
        # Try to log if logger is available
        try:
            logger.error(
                f"Configuration error: {e}",
                extra={"event": "config.error", "error_type": "ConfigurationError"}
            )
        except:
            pass
        return 1
    except KeyboardInterrupt:
        # Graceful exit on Ctrl+C
        print("\nShutdown requested by user", file=sys.stderr)
        return 0
    except Exception as e:
        # Unexpected fatal error
        print(f"Fatal error: {e}", file=sys.stderr)
        # Log with traceback if logger is configured
        try:
            logger.critical(
                "Fatal error during startup",
                extra={
                    "event": "service.startup.failed",
                    "error_type": type(e).__name__,
                    "error": str(e),
                },
                exc_info=True
            )
        except:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
