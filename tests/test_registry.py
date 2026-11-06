"""Unit tests for company registry module."""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch

from app.adapters.registry import CompanyRegistry, expand_sources_with_registry
from app.config.models import SourceConfig


class TestCompanyRegistry:
    """Tests for CompanyRegistry class."""

    def test_load_valid_registry(self, tmp_path):
        """Test loading valid registry file."""
        # Create registry directory
        registry_dir = tmp_path / "registries"
        registry_dir.mkdir()

        # Create valid registry file
        registry_data = {
            "version": "1.0",
            "updated_at": "2025-11-11T00:00:00Z",
            "companies": [
                {"identifier": "test-co", "name": "Test Co", "enabled": True},
                {"identifier": "demo-co", "name": "Demo Co", "enabled": True}
            ]
        }
        (registry_dir / "greenhouse_companies.json").write_text(json.dumps(registry_data))

        # Create empty files for other ATS types to avoid warnings
        (registry_dir / "lever_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))
        (registry_dir / "ashby_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))

        # Load registry
        registry = CompanyRegistry(registry_dir)
        registry.load()

        # Verify companies loaded
        companies = registry.get_companies("greenhouse")
        assert len(companies) == 2
        assert companies[0]["identifier"] == "test-co"
        assert companies[1]["identifier"] == "demo-co"

    def test_load_missing_registry(self, tmp_path):
        """Test loading when registry file doesn't exist."""
        registry_dir = tmp_path / "registries"
        registry_dir.mkdir()

        registry = CompanyRegistry(registry_dir)
        registry.load()

        # Should return empty list, not crash
        companies = registry.get_companies("greenhouse")
        assert companies == []

    def test_load_invalid_json(self, tmp_path):
        """Test loading registry file with invalid JSON."""
        registry_dir = tmp_path / "registries"
        registry_dir.mkdir()

        # Create file with invalid JSON
        (registry_dir / "greenhouse_companies.json").write_text("{ invalid json }")
        (registry_dir / "lever_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))
        (registry_dir / "ashby_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))

        registry = CompanyRegistry(registry_dir)
        registry.load()

        # Should return empty list, not crash
        companies = registry.get_companies("greenhouse")
        assert companies == []

    def test_load_missing_companies_key(self, tmp_path):
        """Test loading registry file missing 'companies' key."""
        registry_dir = tmp_path / "registries"
        registry_dir.mkdir()

        # Create registry without 'companies' key
        registry_data = {
            "version": "1.0",
            "updated_at": "2025-11-11T00:00:00Z"
        }
        (registry_dir / "greenhouse_companies.json").write_text(json.dumps(registry_data))
        (registry_dir / "lever_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))
        (registry_dir / "ashby_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))

        registry = CompanyRegistry(registry_dir)
        registry.load()

        # Should return empty list
        companies = registry.get_companies("greenhouse")
        assert companies == []

    def test_get_companies_enabled_only(self, tmp_path):
        """Test getting only enabled companies."""
        registry_dir = tmp_path / "registries"
        registry_dir.mkdir()

        registry_data = {
            "version": "1.0",
            "companies": [
                {"identifier": "enabled-co", "name": "Enabled Co", "enabled": True},
                {"identifier": "disabled-co", "name": "Disabled Co", "enabled": False},
                {"identifier": "default-co", "name": "Default Co"}  # No enabled key, should default to True
            ]
        }
        (registry_dir / "greenhouse_companies.json").write_text(json.dumps(registry_data))
        (registry_dir / "lever_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))
        (registry_dir / "ashby_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))

        registry = CompanyRegistry(registry_dir)
        registry.load()

        # Get only enabled
        enabled = registry.get_companies("greenhouse", enabled_only=True)
        assert len(enabled) == 2
        assert all(c["identifier"] in ["enabled-co", "default-co"] for c in enabled)

        # Get all
        all_companies = registry.get_companies("greenhouse", enabled_only=False)
        assert len(all_companies) == 3

    def test_expand_wildcard_source(self, tmp_path):
        """Test expanding a wildcard source."""
        registry_dir = tmp_path / "registries"
        registry_dir.mkdir()

        registry_data = {
            "version": "1.0",
            "companies": [
                {"identifier": "company1", "name": "Company One", "enabled": True},
                {"identifier": "company2", "name": "Company Two", "enabled": True},
                {"identifier": "company3", "name": "Company Three", "enabled": True}
            ]
        }
        (registry_dir / "greenhouse_companies.json").write_text(json.dumps(registry_data))
        (registry_dir / "lever_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))
        (registry_dir / "ashby_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))

        registry = CompanyRegistry(registry_dir)
        registry.load()

        # Create wildcard source
        wildcard_source = SourceConfig(
            name="All Greenhouse",
            type="greenhouse",
            identifier="*",
            enabled=True
        )

        # Expand
        expanded = registry.expand_wildcard(wildcard_source)

        # Verify expansion
        assert len(expanded) == 3
        assert expanded[0].name == "Company One"
        assert expanded[0].type == "greenhouse"
        assert expanded[0].identifier == "company1"
        assert expanded[0].enabled is True

        assert expanded[1].name == "Company Two"
        assert expanded[2].name == "Company Three"

    def test_expand_non_wildcard_source(self, tmp_path):
        """Test that non-wildcard sources are returned unchanged."""
        registry_dir = tmp_path / "registries"
        registry_dir.mkdir()

        registry = CompanyRegistry(registry_dir)

        # Create non-wildcard source
        explicit_source = SourceConfig(
            name="Specific Company",
            type="greenhouse",
            identifier="github",
            enabled=True
        )

        # Expand (should return as-is)
        result = registry.expand_wildcard(explicit_source)

        assert len(result) == 1
        assert result[0] == explicit_source

    def test_expand_wildcard_no_companies(self, tmp_path):
        """Test expanding wildcard when registry is empty."""
        registry_dir = tmp_path / "registries"
        registry_dir.mkdir()

        # Create empty registry
        (registry_dir / "greenhouse_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))
        (registry_dir / "lever_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))
        (registry_dir / "ashby_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))

        registry = CompanyRegistry(registry_dir)
        registry.load()

        wildcard_source = SourceConfig(
            name="All Greenhouse",
            type="greenhouse",
            identifier="*",
            enabled=True
        )

        # Expand empty registry
        expanded = registry.expand_wildcard(wildcard_source)

        assert expanded == []

    def test_expand_wildcard_disabled_companies(self, tmp_path):
        """Test that disabled companies are not included in expansion."""
        registry_dir = tmp_path / "registries"
        registry_dir.mkdir()

        registry_data = {
            "version": "1.0",
            "companies": [
                {"identifier": "enabled1", "name": "Enabled One", "enabled": True},
                {"identifier": "disabled", "name": "Disabled Co", "enabled": False},
                {"identifier": "enabled2", "name": "Enabled Two", "enabled": True}
            ]
        }
        (registry_dir / "greenhouse_companies.json").write_text(json.dumps(registry_data))
        (registry_dir / "lever_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))
        (registry_dir / "ashby_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))

        registry = CompanyRegistry(registry_dir)
        registry.load()

        wildcard_source = SourceConfig(
            name="All Greenhouse",
            type="greenhouse",
            identifier="*",
            enabled=True
        )

        expanded = registry.expand_wildcard(wildcard_source)

        # Should only have 2 enabled companies
        assert len(expanded) == 2
        assert all(c.identifier in ["enabled1", "enabled2"] for c in expanded)

    def test_expand_preserves_enabled_flag(self, tmp_path):
        """Test that wildcard source's enabled flag is preserved in expansion."""
        registry_dir = tmp_path / "registries"
        registry_dir.mkdir()

        registry_data = {
            "version": "1.0",
            "companies": [
                {"identifier": "company1", "name": "Company One", "enabled": True}
            ]
        }
        (registry_dir / "greenhouse_companies.json").write_text(json.dumps(registry_data))
        (registry_dir / "lever_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))
        (registry_dir / "ashby_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))

        registry = CompanyRegistry(registry_dir)
        registry.load()

        # Wildcard source with enabled=False
        wildcard_source = SourceConfig(
            name="All Greenhouse",
            type="greenhouse",
            identifier="*",
            enabled=False  # Disabled wildcard
        )

        expanded = registry.expand_wildcard(wildcard_source)

        # Expanded source should also be disabled
        assert len(expanded) == 1
        assert expanded[0].enabled is False

    def test_load_idempotent(self, tmp_path):
        """Test that calling load() multiple times is safe."""
        registry_dir = tmp_path / "registries"
        registry_dir.mkdir()

        registry_data = {
            "version": "1.0",
            "companies": [
                {"identifier": "test-co", "name": "Test Co", "enabled": True}
            ]
        }
        (registry_dir / "greenhouse_companies.json").write_text(json.dumps(registry_data))
        (registry_dir / "lever_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))
        (registry_dir / "ashby_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))

        registry = CompanyRegistry(registry_dir)

        # Load multiple times
        registry.load()
        registry.load()
        registry.load()

        # Should still work correctly
        companies = registry.get_companies("greenhouse")
        assert len(companies) == 1


class TestExpandSourcesWithRegistry:
    """Tests for expand_sources_with_registry function."""

    def test_expand_sources_with_wildcard(self, tmp_path):
        """Test expanding source list with wildcard."""
        registry_dir = tmp_path / "registries"
        registry_dir.mkdir()

        registry_data = {
            "version": "1.0",
            "companies": [
                {"identifier": "co1", "name": "Company 1", "enabled": True},
                {"identifier": "co2", "name": "Company 2", "enabled": True}
            ]
        }
        (registry_dir / "greenhouse_companies.json").write_text(json.dumps(registry_data))
        (registry_dir / "lever_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))
        (registry_dir / "ashby_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))

        # Create registry
        registry = CompanyRegistry(registry_dir)
        registry.load()

        # Sources with wildcard
        sources = [
            SourceConfig(name="All Greenhouse", type="greenhouse", identifier="*", enabled=True)
        ]

        # Expand
        expanded = expand_sources_with_registry(sources, registry)

        assert len(expanded) == 2
        assert expanded[0].identifier == "co1"
        assert expanded[1].identifier == "co2"

    def test_expand_sources_without_wildcard(self, tmp_path):
        """Test that non-wildcard sources are unchanged."""
        registry_dir = tmp_path / "registries"
        registry_dir.mkdir()

        registry = CompanyRegistry(registry_dir)

        # Sources without wildcard
        sources = [
            SourceConfig(name="GitHub", type="greenhouse", identifier="github", enabled=True),
            SourceConfig(name="Stripe", type="greenhouse", identifier="stripe", enabled=True)
        ]

        # Expand
        expanded = expand_sources_with_registry(sources, registry)

        # Should be unchanged
        assert len(expanded) == 2
        assert expanded[0].identifier == "github"
        assert expanded[1].identifier == "stripe"

    def test_expand_sources_mixed(self, tmp_path):
        """Test expanding with mix of wildcard and explicit sources."""
        registry_dir = tmp_path / "registries"
        registry_dir.mkdir()

        greenhouse_data = {
            "version": "1.0",
            "companies": [
                {"identifier": "reg1", "name": "Registry 1", "enabled": True},
                {"identifier": "reg2", "name": "Registry 2", "enabled": True}
            ]
        }
        lever_data = {
            "version": "1.0",
            "companies": [
                {"identifier": "lever1", "name": "Lever 1", "enabled": True}
            ]
        }
        (registry_dir / "greenhouse_companies.json").write_text(json.dumps(greenhouse_data))
        (registry_dir / "lever_companies.json").write_text(json.dumps(lever_data))
        (registry_dir / "ashby_companies.json").write_text(json.dumps({"version": "1.0", "companies": []}))

        registry = CompanyRegistry(registry_dir)
        registry.load()

        # Mixed sources
        sources = [
            SourceConfig(name="Explicit", type="ashby", identifier="anthropic", enabled=True),
            SourceConfig(name="All Greenhouse", type="greenhouse", identifier="*", enabled=True),
            SourceConfig(name="Another Explicit", type="lever", identifier="netflix", enabled=True)
        ]

        expanded = expand_sources_with_registry(sources, registry)

        # Should have: 1 explicit ashby + 2 greenhouse expanded + 1 explicit lever = 4
        assert len(expanded) == 4
        identifiers = [s.identifier for s in expanded]
        assert "anthropic" in identifiers
        assert "reg1" in identifiers
        assert "reg2" in identifiers
        assert "netflix" in identifiers

    def test_expand_sources_default_registry(self):
        """Test that function creates default registry if none provided."""
        # Sources without wildcard (so it won't try to load files)
        sources = [
            SourceConfig(name="GitHub", type="greenhouse", identifier="github", enabled=True)
        ]

        # Call without registry parameter
        expanded = expand_sources_with_registry(sources)

        # Should work and return unchanged sources
        assert len(expanded) == 1
        assert expanded[0].identifier == "github"
