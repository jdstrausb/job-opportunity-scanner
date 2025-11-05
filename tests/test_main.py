"""Unit tests for the main entry point.

Tests the main() function including:
- CLI argument parsing
- Configuration loading with priority (CLI > env > config)
- Logging configuration
- Database initialization
- Service instantiation
- Manual run mode vs daemon mode
- Exit code handling
- Error handling
"""

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from app.config.exceptions import ConfigurationError
from app.main import load_runtime_config, main


class TestLoadRuntimeConfig:
    """Test suite for load_runtime_config helper."""

    def test_load_runtime_config_computes_scan_interval(self, tmp_path):
        """Test that scan_interval is parsed and stored."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
sources:
  - name: Test
    type: greenhouse
    identifier: test
search_criteria:
  required_terms: ["python"]
scan_interval: "PT30M"
email:
  sender_email: test@example.com
"""
        )

        # Mock environment config loading
        with patch("app.main.load_config") as mock_load:
            from app.config.environment import EnvironmentConfig
            from app.config.models import AppConfig, EmailConfig, SearchCriteria, SourceConfig

            mock_app_config = AppConfig(
                sources=[SourceConfig(name="Test", type="greenhouse", identifier="test")],
                search_criteria=SearchCriteria(required_terms=["python"]),
                scan_interval="PT30M",
                email=EmailConfig(sender_email="test@example.com"),
            )
            mock_env_config = EnvironmentConfig(
                smtp_host="smtp.example.com",
                smtp_port=587,
                smtp_user=None,
                smtp_pass=None,
                alert_to_email="test@example.com",
            )
            mock_load.return_value = (mock_app_config, mock_env_config)

            app_config, env_config = load_runtime_config(config_file, None)

            # Should have computed scan_interval_seconds
            assert app_config.scan_interval_seconds == 1800  # 30 minutes

    def test_load_runtime_config_log_level_priority(self, tmp_path):
        """Test log level priority: CLI > env > config."""
        config_file = tmp_path / "config.yaml"

        with patch("app.main.load_config") as mock_load:
            from app.config.environment import EnvironmentConfig
            from app.config.models import (
                AppConfig,
                EmailConfig,
                LoggingConfig,
                SearchCriteria,
                SourceConfig,
            )

            mock_app_config = AppConfig(
                sources=[SourceConfig(name="Test", type="greenhouse", identifier="test")],
                search_criteria=SearchCriteria(required_terms=["python"]),
                scan_interval="15m",
                email=EmailConfig(sender_email="test@example.com"),
                logging=LoggingConfig(level="WARNING", format="key-value"),
            )
            mock_env_config = EnvironmentConfig(
                smtp_host="smtp.example.com",
                smtp_port=587,
                smtp_user=None,
                smtp_pass=None,
                alert_to_email="test@example.com",
                log_level="INFO",  # Env override
            )
            mock_load.return_value = (mock_app_config, mock_env_config)

            # CLI override takes precedence
            _, env_config = load_runtime_config(config_file, "DEBUG")
            assert env_config.log_level == "DEBUG"

            # Env override takes precedence over config
            _, env_config = load_runtime_config(config_file, None)
            assert env_config.log_level == "INFO"

            # Config value used when no overrides
            mock_env_config.log_level = None
            _, env_config = load_runtime_config(config_file, None)
            assert env_config.log_level == "WARNING"

    def test_load_runtime_config_invalid_scan_interval(self, tmp_path):
        """Test that invalid scan_interval raises ConfigurationError."""
        config_file = tmp_path / "config.yaml"

        with patch("app.main.load_config") as mock_load:
            from app.config.environment import EnvironmentConfig
            from app.config.models import AppConfig, EmailConfig, SearchCriteria, SourceConfig

            mock_app_config = AppConfig(
                sources=[SourceConfig(name="Test", type="greenhouse", identifier="test")],
                search_criteria=SearchCriteria(required_terms=["python"]),
                scan_interval="invalid",
                email=EmailConfig(sender_email="test@example.com"),
            )
            mock_env_config = EnvironmentConfig(
                smtp_host="smtp.example.com",
                smtp_port=587,
                smtp_user=None,
                smtp_pass=None,
                alert_to_email="test@example.com",
            )
            mock_load.return_value = (mock_app_config, mock_env_config)

            with pytest.raises(ConfigurationError):
                load_runtime_config(config_file, None)


class TestMain:
    """Test suite for main() function."""

    @patch("app.main.ScanPipeline")
    @patch("app.main.KeywordMatcher")
    @patch("app.main.NotificationService")
    @patch("app.main.init_database")
    @patch("app.main.close_database")
    @patch("app.main.configure_logging")
    @patch("app.main.load_runtime_config")
    @patch("sys.argv", ["job-scanner", "--manual-run", "--config", "config.yaml"])
    def test_main_manual_run_success(
        self,
        mock_load_config,
        mock_configure_logging,
        mock_init_db,
        mock_close_db,
        mock_notification_service,
        mock_keyword_matcher,
        mock_scan_pipeline,
    ):
        """Test main() in manual run mode with successful execution."""
        from app.config.environment import EnvironmentConfig
        from app.config.models import AppConfig, EmailConfig, SearchCriteria, SourceConfig
        from app.pipeline import PipelineRunResult
        from app.utils.timestamps import utc_now

        # Setup mocks
        mock_app_config = AppConfig(
            sources=[SourceConfig(name="Test", type="greenhouse", identifier="test")],
            search_criteria=SearchCriteria(required_terms=["python"]),
            scan_interval="15m",
            scan_interval_seconds=900,
            email=EmailConfig(sender_email="test@example.com"),
        )
        mock_env_config = EnvironmentConfig(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user=None,
            smtp_pass=None,
            alert_to_email="test@example.com",
            log_level="INFO",
        )
        mock_load_config.return_value = (mock_app_config, mock_env_config)

        # Mock pipeline result (success)
        now = utc_now()
        mock_result = PipelineRunResult(
            run_started_at=now,
            run_finished_at=now,
            had_errors=False,
        )
        mock_pipeline_instance = Mock()
        mock_pipeline_instance.run_once.return_value = mock_result
        mock_scan_pipeline.return_value = mock_pipeline_instance

        # Run main
        exit_code = main()

        # Assertions
        assert exit_code == 0
        mock_configure_logging.assert_called_once()
        mock_init_db.assert_called_once()
        mock_close_db.assert_called_once()
        mock_pipeline_instance.run_once.assert_called_once()

    @patch("app.main.ScanPipeline")
    @patch("app.main.KeywordMatcher")
    @patch("app.main.NotificationService")
    @patch("app.main.init_database")
    @patch("app.main.close_database")
    @patch("app.main.configure_logging")
    @patch("app.main.load_runtime_config")
    @patch("sys.argv", ["job-scanner", "--manual-run"])
    def test_main_manual_run_with_errors(
        self,
        mock_load_config,
        mock_configure_logging,
        mock_init_db,
        mock_close_db,
        mock_notification_service,
        mock_keyword_matcher,
        mock_scan_pipeline,
    ):
        """Test main() in manual run mode with errors."""
        from app.config.environment import EnvironmentConfig
        from app.config.models import AppConfig, EmailConfig, SearchCriteria, SourceConfig
        from app.pipeline import PipelineRunResult
        from app.utils.timestamps import utc_now

        mock_app_config = AppConfig(
            sources=[SourceConfig(name="Test", type="greenhouse", identifier="test")],
            search_criteria=SearchCriteria(required_terms=["python"]),
            scan_interval="15m",
            scan_interval_seconds=900,
            email=EmailConfig(sender_email="test@example.com"),
        )
        mock_env_config = EnvironmentConfig(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user=None,
            smtp_pass=None,
            alert_to_email="test@example.com",
            log_level="INFO",
        )
        mock_load_config.return_value = (mock_app_config, mock_env_config)

        # Mock pipeline result (with errors)
        now = utc_now()
        mock_result = PipelineRunResult(
            run_started_at=now,
            run_finished_at=now,
            had_errors=True,
        )
        mock_pipeline_instance = Mock()
        mock_pipeline_instance.run_once.return_value = mock_result
        mock_scan_pipeline.return_value = mock_pipeline_instance

        # Run main
        exit_code = main()

        # Should return 1 due to errors
        assert exit_code == 1

    @patch("app.main.SchedulerService")
    @patch("app.main.ScanPipeline")
    @patch("app.main.KeywordMatcher")
    @patch("app.main.NotificationService")
    @patch("app.main.init_database")
    @patch("app.main.close_database")
    @patch("app.main.configure_logging")
    @patch("app.main.load_runtime_config")
    @patch("signal.signal")
    @patch("sys.argv", ["job-scanner"])
    def test_main_daemon_mode(
        self,
        mock_signal,
        mock_load_config,
        mock_configure_logging,
        mock_init_db,
        mock_close_db,
        mock_notification_service,
        mock_keyword_matcher,
        mock_scan_pipeline,
        mock_scheduler_service,
    ):
        """Test main() in daemon mode."""
        from app.config.environment import EnvironmentConfig
        from app.config.models import AppConfig, EmailConfig, SearchCriteria, SourceConfig

        mock_app_config = AppConfig(
            sources=[SourceConfig(name="Test", type="greenhouse", identifier="test")],
            search_criteria=SearchCriteria(required_terms=["python"]),
            scan_interval="15m",
            scan_interval_seconds=900,
            email=EmailConfig(sender_email="test@example.com"),
        )
        mock_env_config = EnvironmentConfig(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user=None,
            smtp_pass=None,
            alert_to_email="test@example.com",
            log_level="INFO",
        )
        mock_load_config.return_value = (mock_app_config, mock_env_config)

        # Mock scheduler
        mock_scheduler_instance = Mock()
        mock_scheduler_service.return_value = mock_scheduler_instance

        # Simulate immediate shutdown (so test doesn't hang)
        def immediate_shutdown(*args, **kwargs):
            # Trigger KeyboardInterrupt to exit cleanly
            raise KeyboardInterrupt()

        mock_scheduler_instance.start.side_effect = immediate_shutdown

        # Run main
        exit_code = main()

        # Should start scheduler
        mock_scheduler_instance.start.assert_called_once()
        # Should exit cleanly
        assert exit_code == 0

    @patch("app.main.load_runtime_config")
    @patch("sys.argv", ["job-scanner", "--config", "nonexistent.yaml"])
    def test_main_configuration_error(self, mock_load_config):
        """Test main() handles ConfigurationError gracefully."""
        mock_load_config.side_effect = ConfigurationError(
            "Config file not found",
            suggestions=["Create config.yaml"],
        )

        exit_code = main()

        # Should return 1 on configuration error
        assert exit_code == 1

    @patch("app.main.load_runtime_config")
    @patch("sys.argv", ["job-scanner"])
    def test_main_keyboard_interrupt(self, mock_load_config):
        """Test main() handles KeyboardInterrupt gracefully."""
        mock_load_config.side_effect = KeyboardInterrupt()

        exit_code = main()

        # Should return 0 on keyboard interrupt
        assert exit_code == 0

    @patch("app.main.configure_logging")
    @patch("app.main.load_runtime_config")
    @patch("sys.argv", ["job-scanner", "--log-level", "DEBUG"])
    def test_main_log_level_override(self, mock_load_config, mock_configure_logging):
        """Test that --log-level is passed to load_runtime_config."""
        from app.config.environment import EnvironmentConfig
        from app.config.models import AppConfig, EmailConfig, SearchCriteria, SourceConfig

        mock_app_config = AppConfig(
            sources=[SourceConfig(name="Test", type="greenhouse", identifier="test")],
            search_criteria=SearchCriteria(required_terms=["python"]),
            scan_interval="15m",
            scan_interval_seconds=900,
            email=EmailConfig(sender_email="test@example.com"),
        )
        mock_env_config = EnvironmentConfig(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user=None,
            smtp_pass=None,
            alert_to_email="test@example.com",
            log_level="DEBUG",
        )
        mock_load_config.return_value = (mock_app_config, mock_env_config)

        # Raise exception to exit early
        mock_configure_logging.side_effect = Exception("exit early")

        try:
            main()
        except Exception:
            pass

        # Verify log level was passed
        mock_load_config.assert_called_once()
        call_args = mock_load_config.call_args[0]
        assert call_args[1] == "DEBUG"  # log_level_override
