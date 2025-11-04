"""Integration tests for configuration module."""

import os
from pathlib import Path

import pytest

from app.config import (
    ATSType,
    ConfigurationError,
    load_config,
    validate_config_file,
)
from app.config.duration import DurationParseError, parse_duration, validate_duration_range
from app.config.environment import load_environment_config


# Test fixtures directory
FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestConfigurationLoading:
    """Test configuration loading from YAML files."""

    def test_load_valid_config(self, mock_env_vars):
        """Test loading a valid configuration file."""
        config_path = FIXTURES_DIR / "valid_config.yaml"
        app_config, env_config = load_config(config_path)

        # Verify sources
        assert len(app_config.sources) == 2
        assert app_config.sources[0].name == "Test Company A"
        assert app_config.sources[0].type == ATSType.GREENHOUSE
        assert app_config.sources[0].identifier == "testcompanya"
        assert app_config.sources[0].enabled is True

        # Verify search criteria
        assert "python" in app_config.search_criteria.required_terms
        assert "backend" in app_config.search_criteria.required_terms
        assert len(app_config.search_criteria.keyword_groups) == 2

        # Verify scan interval
        assert app_config.scan_interval == "15m"
        assert app_config.scan_interval_seconds == 900

        # Verify email config
        assert app_config.email.use_tls is True
        assert app_config.email.max_retries == 3

        # Verify logging config
        assert app_config.logging.level == "INFO"
        assert app_config.logging.format == "key-value"

    def test_load_minimal_config(self, mock_env_vars):
        """Test loading a minimal configuration with defaults."""
        config_path = FIXTURES_DIR / "minimal_config.yaml"
        app_config, env_config = load_config(config_path)

        # Verify required fields
        assert len(app_config.sources) == 1
        assert app_config.sources[0].name == "Minimal Test Company"

        # Verify defaults are applied
        assert app_config.scan_interval == "15m"  # Default
        assert app_config.email.max_retries == 3  # Default
        assert app_config.logging.level == "INFO"  # Default

    def test_load_iso8601_duration_config(self, mock_env_vars):
        """Test loading config with ISO-8601 duration format."""
        config_path = FIXTURES_DIR / "iso8601_duration_config.yaml"
        app_config, env_config = load_config(config_path)

        assert app_config.scan_interval == "PT15M"
        assert app_config.scan_interval_seconds == 900  # 15 minutes

    def test_config_file_not_found(self, mock_env_vars):
        """Test error when config file doesn't exist."""
        with pytest.raises(ConfigurationError) as exc_info:
            load_config(Path("nonexistent.yaml"))

        assert "not found" in str(exc_info.value).lower()
        assert "config.example.yaml" in str(exc_info.value)

    def test_invalid_yaml_syntax(self, tmp_path, mock_env_vars):
        """Test error when YAML syntax is invalid."""
        invalid_yaml = tmp_path / "invalid.yaml"
        invalid_yaml.write_text("sources:\n  - name: 'test\n    invalid yaml")

        with pytest.raises(ConfigurationError) as exc_info:
            load_config(invalid_yaml)

        assert "parse" in str(exc_info.value).lower()


class TestConfigurationValidation:
    """Test configuration validation rules."""

    def test_missing_required_field_sources(self, mock_env_vars):
        """Test error when sources field is missing."""
        config_path = FIXTURES_DIR / "invalid_missing_sources.yaml"

        with pytest.raises(ConfigurationError) as exc_info:
            load_config(config_path)

        error_msg = str(exc_info.value)
        assert "missing" in error_msg.lower() or "required" in error_msg.lower()

    def test_empty_search_criteria(self, mock_env_vars):
        """Test error when search criteria is empty."""
        config_path = FIXTURES_DIR / "invalid_empty_search.yaml"

        with pytest.raises(ConfigurationError) as exc_info:
            load_config(config_path)

        error_msg = str(exc_info.value)
        assert "search criteria" in error_msg.lower()

    def test_invalid_ats_type(self, mock_env_vars):
        """Test error when ATS type is invalid."""
        config_path = FIXTURES_DIR / "invalid_bad_ats_type.yaml"

        with pytest.raises(ConfigurationError) as exc_info:
            load_config(config_path)

        error_msg = str(exc_info.value)
        assert "type" in error_msg.lower()

    def test_duplicate_sources(self, mock_env_vars):
        """Test error when duplicate sources exist."""
        config_path = FIXTURES_DIR / "invalid_duplicate_sources.yaml"

        with pytest.raises(ConfigurationError) as exc_info:
            load_config(config_path)

        error_msg = str(exc_info.value)
        assert "duplicate" in error_msg.lower()

    def test_conflicting_terms(self, mock_env_vars):
        """Test error when terms conflict between required and excluded."""
        config_path = FIXTURES_DIR / "invalid_conflicting_terms.yaml"

        with pytest.raises(ConfigurationError) as exc_info:
            load_config(config_path)

        error_msg = str(exc_info.value)
        assert "remote" in error_msg.lower()

    def test_scan_interval_too_short(self, mock_env_vars):
        """Test error when scan interval is too short."""
        config_path = FIXTURES_DIR / "invalid_scan_interval.yaml"

        with pytest.raises(ConfigurationError) as exc_info:
            load_config(config_path)

        error_msg = str(exc_info.value)
        assert "short" in error_msg.lower() or "minimum" in error_msg.lower()


class TestDurationParsing:
    """Test duration parsing utilities."""

    def test_parse_human_readable_minutes(self):
        """Test parsing minutes in human-readable format."""
        assert parse_duration("15m") == 900
        assert parse_duration("30m") == 1800
        assert parse_duration("60m") == 3600

    def test_parse_human_readable_hours(self):
        """Test parsing hours in human-readable format."""
        assert parse_duration("1h") == 3600
        assert parse_duration("2h") == 7200
        assert parse_duration("24h") == 86400

    def test_parse_human_readable_seconds(self):
        """Test parsing seconds in human-readable format."""
        assert parse_duration("30s") == 30
        assert parse_duration("300s") == 300

    def test_parse_human_readable_days(self):
        """Test parsing days in human-readable format."""
        assert parse_duration("1d") == 86400
        assert parse_duration("2d") == 172800

    def test_parse_human_readable_combined(self):
        """Test parsing combined units."""
        assert parse_duration("1h30m") == 5400  # 90 minutes
        assert parse_duration("2h15m") == 8100

    def test_parse_iso8601_minutes(self):
        """Test parsing ISO-8601 minutes."""
        assert parse_duration("PT15M") == 900
        assert parse_duration("PT30M") == 1800

    def test_parse_iso8601_hours(self):
        """Test parsing ISO-8601 hours."""
        assert parse_duration("PT1H") == 3600
        assert parse_duration("PT2H") == 7200

    def test_parse_iso8601_combined(self):
        """Test parsing ISO-8601 combined units."""
        assert parse_duration("PT1H30M") == 5400

    def test_parse_iso8601_days(self):
        """Test parsing ISO-8601 days."""
        assert parse_duration("P1D") == 86400
        assert parse_duration("P2D") == 172800

    def test_parse_invalid_format(self):
        """Test error on invalid duration format."""
        with pytest.raises(DurationParseError):
            parse_duration("invalid")

        with pytest.raises(DurationParseError):
            parse_duration("15x")  # Invalid unit

    def test_parse_empty_string(self):
        """Test error on empty duration string."""
        with pytest.raises(DurationParseError):
            parse_duration("")

    def test_validate_duration_range_too_short(self):
        """Test validation error when duration is too short."""
        with pytest.raises(DurationParseError) as exc_info:
            validate_duration_range(120, min_seconds=300)  # 2 minutes < 5 minutes

        assert "short" in str(exc_info.value).lower()

    def test_validate_duration_range_too_long(self):
        """Test validation error when duration is too long."""
        with pytest.raises(DurationParseError) as exc_info:
            validate_duration_range(
                172800, max_seconds=86400
            )  # 2 days > 1 day

        assert "long" in str(exc_info.value).lower()

    def test_validate_duration_range_valid(self):
        """Test validation passes for valid duration."""
        # Should not raise
        validate_duration_range(900, min_seconds=300, max_seconds=86400)  # 15 minutes


class TestEnvironmentVariables:
    """Test environment variable loading and validation."""

    def test_load_valid_environment_config(self, mock_env_vars):
        """Test loading valid environment configuration."""
        env_config = load_environment_config()

        assert env_config.smtp_host == "smtp.test.com"
        assert env_config.smtp_port == 587
        assert env_config.alert_to_email == "test@example.com"

    def test_missing_required_env_var(self, monkeypatch):
        """Test error when required environment variable is missing."""
        # Clear all SMTP env vars
        monkeypatch.delenv("SMTP_HOST", raising=False)
        monkeypatch.delenv("SMTP_PORT", raising=False)
        monkeypatch.delenv("ALERT_TO_EMAIL", raising=False)

        with pytest.raises(ConfigurationError) as exc_info:
            load_environment_config()

        error_msg = str(exc_info.value)
        assert "SMTP_HOST" in error_msg
        assert "SMTP_PORT" in error_msg
        assert "ALERT_TO_EMAIL" in error_msg

    def test_invalid_smtp_port(self, monkeypatch):
        """Test error when SMTP_PORT is invalid."""
        monkeypatch.setenv("SMTP_HOST", "smtp.test.com")
        monkeypatch.setenv("SMTP_PORT", "invalid")
        monkeypatch.setenv("ALERT_TO_EMAIL", "test@example.com")

        with pytest.raises(ConfigurationError) as exc_info:
            load_environment_config()

        assert "SMTP_PORT" in str(exc_info.value)

    def test_invalid_email_format(self, monkeypatch):
        """Test error when email format is invalid."""
        monkeypatch.setenv("SMTP_HOST", "smtp.test.com")
        monkeypatch.setenv("SMTP_PORT", "587")
        monkeypatch.setenv("ALERT_TO_EMAIL", "invalid-email")

        with pytest.raises(ConfigurationError) as exc_info:
            load_environment_config()

        assert "email" in str(exc_info.value).lower()

    def test_optional_env_vars(self, monkeypatch):
        """Test that optional environment variables work."""
        monkeypatch.setenv("SMTP_HOST", "smtp.test.com")
        monkeypatch.setenv("SMTP_PORT", "587")
        monkeypatch.setenv("ALERT_TO_EMAIL", "test@example.com")
        monkeypatch.setenv("SMTP_USER", "user@test.com")
        monkeypatch.setenv("SMTP_PASS", "password123")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")

        env_config = load_environment_config()

        assert env_config.smtp_user == "user@test.com"
        assert env_config.smtp_pass == "password123"
        assert env_config.log_level == "DEBUG"


class TestConfigurationHelpers:
    """Test configuration helper methods."""

    def test_get_enabled_sources(self, mock_env_vars):
        """Test getting only enabled sources."""
        config_path = FIXTURES_DIR / "valid_config.yaml"
        app_config, _ = load_config(config_path)

        enabled = app_config.get_enabled_sources()
        assert len(enabled) == 2
        assert all(source.enabled for source in enabled)

    def test_validate_config_file_utility(self):
        """Test the standalone config validation utility."""
        config_path = FIXTURES_DIR / "valid_config.yaml"
        assert validate_config_file(config_path) is True

        invalid_path = FIXTURES_DIR / "invalid_missing_sources.yaml"
        assert validate_config_file(invalid_path) is False


# Pytest fixtures
@pytest.fixture
def mock_env_vars(monkeypatch):
    """Mock required environment variables for testing."""
    monkeypatch.setenv("SMTP_HOST", "smtp.test.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "user@test.com")
    monkeypatch.setenv("SMTP_PASS", "testpass123")
    monkeypatch.setenv("ALERT_TO_EMAIL", "test@example.com")
