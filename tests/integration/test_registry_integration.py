"""Integration tests for registry system with config loading."""

import json
import pytest
from pathlib import Path

from app.config.loader import load_config
from app.config.exceptions import ConfigurationError


class TestRegistryConfigIntegration:
    """Integration tests for registry wildcard expansion during config loading."""

    def test_load_config_with_wildcard_source(self, tmp_path, monkeypatch):
        """Test config loading with wildcard source expands from registry."""
        # Create config file with wildcard
        config_content = """
sources:
  - name: "All Greenhouse Companies"
    type: "greenhouse"
    identifier: "*"
    enabled: true

search_criteria:
  required_terms:
    - "python"
  keyword_groups:
    - ["backend", "full-stack"]

scan_interval: "15m"
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        # Create registry directory and files
        registry_dir = tmp_path / "data" / "registries"
        registry_dir.mkdir(parents=True)

        greenhouse_data = {
            "version": "1.0",
            "companies": [
                {"identifier": "test1", "name": "Test Company 1", "enabled": True},
                {"identifier": "test2", "name": "Test Company 2", "enabled": True}
            ]
        }
        (registry_dir / "greenhouse_companies.json").write_text(json.dumps(greenhouse_data))
        (registry_dir / "lever_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))
        (registry_dir / "ashby_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))

        # Mock environment variables
        monkeypatch.setenv("SMTP_HOST", "localhost")
        monkeypatch.setenv("SMTP_PORT", "587")
        monkeypatch.setenv("ALERT_TO_EMAIL", "test@example.com")

        # Change working directory to tmp_path for registry path resolution
        monkeypatch.chdir(tmp_path)

        # Load config
        app_config, env_config = load_config(config_file)

        # Verify expansion
        assert len(app_config.sources) == 2
        assert app_config.sources[0].name == "Test Company 1"
        assert app_config.sources[0].identifier == "test1"
        assert app_config.sources[1].name == "Test Company 2"
        assert app_config.sources[1].identifier == "test2"

    def test_load_config_without_wildcard_unchanged(self, tmp_path, monkeypatch):
        """Test that configs without wildcards are unchanged."""
        # Create config file with explicit sources
        config_content = """
sources:
  - name: "GitHub"
    type: "greenhouse"
    identifier: "github"
    enabled: true
  - name: "Stripe"
    type: "greenhouse"
    identifier: "stripe"
    enabled: true

search_criteria:
  required_terms:
    - "python"

scan_interval: "15m"
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        # Mock environment variables
        monkeypatch.setenv("SMTP_HOST", "localhost")
        monkeypatch.setenv("SMTP_PORT", "587")
        monkeypatch.setenv("ALERT_TO_EMAIL", "test@example.com")

        # Load config
        app_config, env_config = load_config(config_file)

        # Verify no expansion (sources unchanged)
        assert len(app_config.sources) == 2
        assert app_config.sources[0].name == "GitHub"
        assert app_config.sources[0].identifier == "github"
        assert app_config.sources[1].name == "Stripe"
        assert app_config.sources[1].identifier == "stripe"

    def test_load_config_mixed_wildcard_and_explicit(self, tmp_path, monkeypatch):
        """Test config with both wildcard and explicit sources."""
        config_content = """
sources:
  - name: "Explicit Company"
    type: "lever"
    identifier: "netflix"
    enabled: true
  - name: "All Greenhouse"
    type: "greenhouse"
    identifier: "*"
    enabled: true

search_criteria:
  required_terms:
    - "engineer"

scan_interval: "15m"
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        # Create registry
        registry_dir = tmp_path / "data" / "registries"
        registry_dir.mkdir(parents=True)

        greenhouse_data = {
            "version": "1.0",
            "companies": [
                {"identifier": "gh1", "name": "GH Company 1", "enabled": True},
                {"identifier": "gh2", "name": "GH Company 2", "enabled": True}
            ]
        }
        (registry_dir / "greenhouse_companies.json").write_text(json.dumps(greenhouse_data))
        (registry_dir / "lever_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))
        (registry_dir / "ashby_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))

        # Mock environment
        monkeypatch.setenv("SMTP_HOST", "localhost")
        monkeypatch.setenv("SMTP_PORT", "587")
        monkeypatch.setenv("ALERT_TO_EMAIL", "test@example.com")
        monkeypatch.chdir(tmp_path)

        # Load config
        app_config, env_config = load_config(config_file)

        # Verify mixed expansion
        assert len(app_config.sources) == 3
        assert app_config.sources[0].identifier == "netflix"  # Explicit preserved
        assert app_config.sources[1].identifier == "gh1"  # Wildcard expanded
        assert app_config.sources[2].identifier == "gh2"  # Wildcard expanded

    def test_load_config_wildcard_registry_missing_graceful(self, tmp_path, monkeypatch):
        """Test graceful degradation when registry files are missing."""
        config_content = """
sources:
  - name: "All Greenhouse"
    type: "greenhouse"
    identifier: "*"
    enabled: true

search_criteria:
  required_terms:
    - "python"

scan_interval: "15m"
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        # NO registry directory created - simulates missing registry

        # Mock environment
        monkeypatch.setenv("SMTP_HOST", "localhost")
        monkeypatch.setenv("SMTP_PORT", "587")
        monkeypatch.setenv("ALERT_TO_EMAIL", "test@example.com")
        monkeypatch.chdir(tmp_path)

        # Load config - should not crash
        app_config, env_config = load_config(config_file)

        # Wildcard expands to empty (no companies found)
        assert len(app_config.sources) == 0

    def test_load_config_wildcard_disabled_companies(self, tmp_path, monkeypatch):
        """Test that disabled companies in registry are not expanded."""
        config_content = """
sources:
  - name: "All Greenhouse"
    type: "greenhouse"
    identifier: "*"
    enabled: true

search_criteria:
  required_terms:
    - "python"

scan_interval: "15m"
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        # Create registry with mix of enabled/disabled
        registry_dir = tmp_path / "data" / "registries"
        registry_dir.mkdir(parents=True)

        greenhouse_data = {
            "version": "1.0",
            "companies": [
                {"identifier": "enabled1", "name": "Enabled 1", "enabled": True},
                {"identifier": "disabled", "name": "Disabled Co", "enabled": False},
                {"identifier": "enabled2", "name": "Enabled 2", "enabled": True}
            ]
        }
        (registry_dir / "greenhouse_companies.json").write_text(json.dumps(greenhouse_data))
        (registry_dir / "lever_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))
        (registry_dir / "ashby_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))

        # Mock environment
        monkeypatch.setenv("SMTP_HOST", "localhost")
        monkeypatch.setenv("SMTP_PORT", "587")
        monkeypatch.setenv("ALERT_TO_EMAIL", "test@example.com")
        monkeypatch.chdir(tmp_path)

        # Load config
        app_config, env_config = load_config(config_file)

        # Only enabled companies should be in the list
        assert len(app_config.sources) == 2
        identifiers = [s.identifier for s in app_config.sources]
        assert "enabled1" in identifiers
        assert "enabled2" in identifiers
        assert "disabled" not in identifiers
