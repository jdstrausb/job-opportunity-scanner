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
            # (load_runtime_config mutates the shared env config, so reset it)
            mock_env_config.log_level = "INFO"
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
                scan_interval="15m",
                email=EmailConfig(sender_email="test@example.com"),
            )
            # AppConfig rejects bad intervals at construction; bypass validation
            # to exercise load_runtime_config's own guard.
            mock_app_config.scan_interval = "invalid"
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

    @pytest.fixture(autouse=True)
    def startup_reevaluation_stub(self):
        """Keep the startup re-evaluation pass off the (mocked) database."""
        with (
            patch("app.main.reevaluate_stored_jobs", return_value=0),
            patch("app.main.get_session"),
        ):
            yield

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
    @patch("sys.argv", ["job-scanner", "--no-ui"])
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


class TestDismissCLI:
    """Tests for dismissal management CLI flags (no SMTP/config required)."""

    @pytest.fixture(autouse=True)
    def cli_env(self, tmp_path, monkeypatch):
        """Point DATABASE_URL at a temp DB and strip SMTP configuration."""
        db_url = f"sqlite:///{tmp_path / 'dismiss.db'}"
        monkeypatch.setenv("DATABASE_URL", db_url)
        for var in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "ALERT_TO_EMAIL"):
            monkeypatch.delenv(var, raising=False)
        self.db_url = db_url
        yield
        from app.persistence.database import close_database

        close_database()

    def _run(self, *argv):
        with patch("sys.argv", ["job-scanner", *argv]):
            return main()

    def _items(self, scope=None):
        from app.persistence.database import close_database, get_session, init_database
        from app.persistence.repositories import DismissedItemRepository

        close_database()
        init_database(self.db_url)
        with get_session() as session:
            return DismissedItemRepository(session).list_all(scope=scope)

    def test_cli_dismiss_job_key_default_scope(self):
        from app.domain.models import DismissalScope

        assert self._run("--dismiss", "ABC123def") == 0
        items = self._items()
        assert len(items) == 1
        assert items[0].item_key == "abc123def"
        assert items[0].scope == DismissalScope.JOB

    def test_cli_dismiss_scoped_prefix(self):
        from app.domain.models import DismissalScope

        code = self._run(
            "--dismiss", "company:acme-corp", "--dismiss-reason", "Recruiter spam"
        )
        assert code == 0
        items = self._items()
        assert len(items) == 1
        assert items[0].item_key == "acme-corp"
        assert items[0].scope == DismissalScope.COMPANY
        assert items[0].reason == "Recruiter spam"

    def test_cli_dismiss_invalid_scope_exits_1(self, capsys):
        assert self._run("--dismiss", "bogus:thing") == 1
        assert "scope" in capsys.readouterr().err.lower()
        assert self._items() == []

    def test_cli_list_dismissed(self, capsys):
        assert self._run("--list-dismissed") == 0
        assert "No dismissed items" in capsys.readouterr().out

        self._run("--dismiss", "source:acme", "--dismiss-reason", "noisy")
        self._run("--dismiss", "company:Foo Inc")
        capsys.readouterr()

        assert self._run("--list-dismissed") == 0
        out = capsys.readouterr().out
        assert "SCOPE" in out and "KEY" in out
        assert "source" in out and "acme" in out and "noisy" in out
        assert "company" in out and "foo inc" in out

    def test_cli_undismiss(self, capsys):
        self._run("--dismiss", "company:acme-corp")
        capsys.readouterr()

        assert self._run("--undismiss", "company:acme-corp") == 0
        assert "Removed" in capsys.readouterr().out
        assert self._items() == []

        assert self._run("--undismiss", "company:acme-corp") == 0
        assert "not found" in capsys.readouterr().out.lower()


class TestCoHostedDaemon:
    """Default daemon mode serves the UI; other modes stay headless."""

    @pytest.fixture
    def runtime(self, monkeypatch):
        from contextlib import ExitStack

        from app.config.environment import EnvironmentConfig
        from app.config.models import AppConfig, EmailConfig, SearchCriteria, SourceConfig

        monkeypatch.delenv("UI_PORT", raising=False)
        monkeypatch.delenv("UI_HOST", raising=False)
        app_config = AppConfig(
            sources=[SourceConfig(name="Test", type="greenhouse", identifier="test")],
            search_criteria=SearchCriteria(required_terms=["python"]),
            scan_interval="15m",
            scan_interval_seconds=900,
            email=EmailConfig(),
        )
        env_config = EnvironmentConfig(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user=None,
            smtp_pass=None,
            alert_to_email="test@example.com",
            log_level="INFO",
        )
        names = [
            "load_runtime_config",
            "configure_logging",
            "init_database",
            "close_database",
            "NotificationService",
            "KeywordMatcher",
            "ScanPipeline",
            "SchedulerService",
            "ConfigManager",
            "create_app",
            "uvicorn",
            "reevaluate_stored_jobs",
            "get_session",
        ]
        with ExitStack() as stack:
            mocks = {n: stack.enter_context(patch(f"app.main.{n}")) for n in names}
            stack.enter_context(patch("signal.signal"))
            mocks["load_runtime_config"].return_value = (app_config, env_config)
            mocks["reevaluate_stored_jobs"].return_value = 0
            from types import SimpleNamespace

            yield SimpleNamespace(**mocks)

    @staticmethod
    def _run(*argv):
        with patch("sys.argv", ["job-scanner", *argv]):
            return main()

    def test_default_daemon_reevaluates_unscored_jobs_then_serves_ui(self, runtime):
        exit_code = self._run("--config", "my.yaml")

        assert exit_code == 0
        runtime.reevaluate_stored_jobs.assert_called_once()
        args, kwargs = runtime.reevaluate_stored_jobs.call_args
        assert args[1] is runtime.KeywordMatcher.return_value
        assert kwargs == {"only_unscored": True}

        runtime.ConfigManager.assert_called_once_with(
            Path("my.yaml"),
            pipeline=runtime.ScanPipeline.return_value,
            scheduler_service=runtime.SchedulerService.return_value,
        )
        runtime.create_app.assert_called_once_with(
            pipeline=runtime.ScanPipeline.return_value,
            scheduler_service=runtime.SchedulerService.return_value,
            config_manager=runtime.ConfigManager.return_value,
            static_dir=Path("frontend/build"),
            manage_scheduler_lifespan=True,
        )
        runtime.uvicorn.run.assert_called_once_with(
            runtime.create_app.return_value,
            host="127.0.0.1",
            port=8000,
            log_config=None,
            ws="none",
        )
        # The lifespan owns the scheduler: main() must not start it directly
        runtime.SchedulerService.return_value.start.assert_not_called()
        runtime.close_database.assert_called_once()

    def test_cli_port_and_host_flags_override_env_and_defaults(self, runtime, monkeypatch):
        monkeypatch.setenv("UI_PORT", "9100")
        monkeypatch.setenv("UI_HOST", "0.0.0.0")
        self._run("--port", "9200", "--host", "127.0.0.2")
        assert runtime.uvicorn.run.call_args.kwargs == {
            "host": "127.0.0.2",
            "port": 9200,
            "log_config": None,
            "ws": "none",
        }

        runtime.uvicorn.run.reset_mock()
        self._run()
        assert runtime.uvicorn.run.call_args.kwargs["port"] == 9100
        assert runtime.uvicorn.run.call_args.kwargs["host"] == "0.0.0.0"

        monkeypatch.delenv("UI_PORT")
        monkeypatch.delenv("UI_HOST")
        runtime.uvicorn.run.reset_mock()
        self._run()
        assert runtime.uvicorn.run.call_args.kwargs["port"] == 8000
        assert runtime.uvicorn.run.call_args.kwargs["host"] == "127.0.0.1"

    def test_invalid_ui_port_env_is_a_usage_error(self, runtime, monkeypatch, capsys):
        monkeypatch.setenv("UI_PORT", "not-a-port")
        with pytest.raises(SystemExit) as excinfo:
            self._run()
        assert excinfo.value.code == 2
        assert "UI_PORT" in capsys.readouterr().err
        runtime.uvicorn.run.assert_not_called()

    def test_no_ui_runs_headless_scheduler_without_uvicorn(self, runtime):
        runtime.SchedulerService.return_value.start.side_effect = KeyboardInterrupt

        exit_code = self._run("--no-ui")

        assert exit_code == 0
        runtime.SchedulerService.return_value.start.assert_called_once()
        runtime.uvicorn.run.assert_not_called()
        runtime.create_app.assert_not_called()
        runtime.reevaluate_stored_jobs.assert_called_once()

    def test_manual_run_never_starts_the_ui(self, runtime):
        from app.pipeline import PipelineRunResult
        from app.utils.timestamps import utc_now

        now = utc_now()
        runtime.ScanPipeline.return_value.run_once.return_value = PipelineRunResult(
            run_started_at=now, run_finished_at=now
        )

        assert self._run("--manual-run") == 0

        runtime.ScanPipeline.return_value.run_once.assert_called_once()
        runtime.uvicorn.run.assert_not_called()
        runtime.create_app.assert_not_called()
        runtime.SchedulerService.assert_not_called()

    def test_dismissal_commands_never_start_the_ui(self, runtime, tmp_path, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'd.db'}")
        runtime.init_database.side_effect = None

        with patch("app.main.init_database"), patch("app.main.close_database"):
            self._run("--list-dismissed")

        runtime.uvicorn.run.assert_not_called()
        runtime.create_app.assert_not_called()
        runtime.load_runtime_config.assert_not_called()

    def test_startup_reevaluation_failure_does_not_block_startup(self, runtime):
        runtime.reevaluate_stored_jobs.side_effect = RuntimeError("db hiccup")

        assert self._run() == 0

        runtime.uvicorn.run.assert_called_once()
