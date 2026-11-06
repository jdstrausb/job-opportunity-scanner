"""Company registry management for multi-company scanning.

This module provides functionality to load company registries from JSON files
and expand wildcard source configurations into concrete source entries.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from app.config.models import ATSType, SourceConfig

logger = logging.getLogger(__name__)


class CompanyRegistry:
    """Manages company registries for ATS providers.

    Loads company lists from JSON files and provides methods to expand
    wildcard source configurations into concrete sources.
    """

    def __init__(self, registry_dir: Path = Path("data/registries")):
        """Initialize registry with directory path.

        Args:
            registry_dir: Directory containing registry JSON files
        """
        self.registry_dir = registry_dir
        self._registries: Dict[str, List[Dict]] = {}
        self._loaded = False

    def load(self) -> None:
        """Load all registry files from disk.

        Loads greenhouse_companies.json, lever_companies.json, and
        ashby_companies.json. Missing files are logged but not fatal.
        """
        if self._loaded:
            return

        ats_types = ["greenhouse", "lever", "ashby"]

        for ats_type in ats_types:
            registry_file = self.registry_dir / f"{ats_type}_companies.json"

            if not registry_file.exists():
                logger.warning(
                    f"Registry file not found: {registry_file}",
                    extra={"event": "registry.load.missing", "ats_type": ats_type}
                )
                self._registries[ats_type] = []
                continue

            try:
                with open(registry_file, "r") as f:
                    data = json.load(f)

                companies = data.get("companies", [])
                self._registries[ats_type] = companies

                enabled_count = sum(1 for c in companies if c.get("enabled", True))

                logger.info(
                    f"Loaded {len(companies)} companies from {ats_type} registry "
                    f"({enabled_count} enabled)",
                    extra={
                        "event": "registry.load.success",
                        "ats_type": ats_type,
                        "total": len(companies),
                        "enabled": enabled_count
                    }
                )

            except (json.JSONDecodeError, IOError) as e:
                logger.error(
                    f"Failed to load registry {registry_file}: {e}",
                    extra={"event": "registry.load.error", "ats_type": ats_type},
                    exc_info=True
                )
                self._registries[ats_type] = []

        self._loaded = True

    def get_companies(self, ats_type: str, enabled_only: bool = True) -> List[Dict]:
        """Get companies for a specific ATS type.

        Args:
            ats_type: ATS platform type (greenhouse, lever, ashby)
            enabled_only: If True, return only enabled companies

        Returns:
            List of company dictionaries with 'identifier', 'name', 'enabled' keys
        """
        if not self._loaded:
            self.load()

        companies = self._registries.get(ats_type, [])

        if enabled_only:
            companies = [c for c in companies if c.get("enabled", True)]

        return companies

    def expand_wildcard(self, source_config: SourceConfig) -> List[SourceConfig]:
        """Expand a wildcard source into concrete sources from registry.

        Args:
            source_config: Source with identifier="*"

        Returns:
            List of concrete SourceConfig objects (one per registry company)
        """
        if source_config.identifier != "*":
            # Not a wildcard, return as-is
            return [source_config]

        companies = self.get_companies(source_config.type, enabled_only=True)

        if not companies:
            logger.warning(
                f"No companies found in registry for {source_config.type}",
                extra={"event": "registry.expand.empty", "ats_type": source_config.type}
            )
            return []

        expanded = []
        for company in companies:
            expanded_source = SourceConfig(
                name=company["name"],
                type=source_config.type,
                identifier=company["identifier"],
                enabled=source_config.enabled
            )
            expanded.append(expanded_source)

        logger.info(
            f"Expanded wildcard {source_config.type} source to {len(expanded)} companies",
            extra={
                "event": "registry.expand.success",
                "ats_type": source_config.type,
                "count": len(expanded)
            }
        )

        return expanded


def expand_sources_with_registry(
    sources: List[SourceConfig],
    registry: Optional[CompanyRegistry] = None
) -> List[SourceConfig]:
    """Expand source list, replacing wildcards with registry entries.

    Args:
        sources: List of source configs (may contain wildcards)
        registry: Registry instance (creates default if None)

    Returns:
        Expanded list with wildcards replaced by concrete sources
    """
    if registry is None:
        registry = CompanyRegistry()
        registry.load()

    expanded = []

    for source in sources:
        if source.identifier == "*":
            # Expand wildcard
            expanded.extend(registry.expand_wildcard(source))
        else:
            # Keep explicit source
            expanded.append(source)

    return expanded
