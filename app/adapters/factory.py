"""Factory function for instantiating ATS adapters."""

import logging

from typing import Optional

from app.config.environment import EnvironmentConfig
from app.config.models import AdvancedConfig, SourceConfig

from .ashby import AshbyAdapter
from .base import BaseAdapter
from .exceptions import AdapterConfigurationError
from .greenhouse import GreenhouseAdapter
from .lever import LeverAdapter
from .rss import RssAdapter
from .serpapi_google import SerpApiSearchAdapter

logger = logging.getLogger(__name__)


def get_adapter(
    source_config: SourceConfig,
    advanced_config: AdvancedConfig,
    env_config: Optional[EnvironmentConfig] = None,
) -> BaseAdapter:
    """Factory function to instantiate the appropriate ATS adapter.

    Creates an adapter instance for the specified ATS type with configuration
    from advanced_config (timeout, user-agent, max_jobs).

    Args:
        source_config: Source configuration with ATS type and identifier
        advanced_config: Advanced configuration with timeout and user-agent settings
        env_config: Optional environment configuration (supplies the SerpAPI key)

    Returns:
        Instantiated adapter for the specified ATS type

    Raises:
        AdapterConfigurationError: If ATS type is not supported or config is invalid

    Example:
        >>> source = SourceConfig(name="Example", type="greenhouse", identifier="example")
        >>> config = AdvancedConfig()
        >>> adapter = get_adapter(source, config)
        >>> jobs = adapter.fetch_jobs(source)
    """
    # Map of ATS types to adapter classes
    adapter_map: dict[str, type[BaseAdapter]] = {
        "greenhouse": GreenhouseAdapter,
        "lever": LeverAdapter,
        "ashby": AshbyAdapter,
        "rss": RssAdapter,
        "serpapi_google": SerpApiSearchAdapter,
    }

    # Get the adapter class for the source type
    ats_type = source_config.type.lower() if isinstance(source_config.type, str) else str(source_config.type)
    adapter_class = adapter_map.get(ats_type)

    if not adapter_class:
        supported_types = ", ".join(sorted(adapter_map.keys()))
        raise AdapterConfigurationError(
            f"Unknown ATS type: {source_config.type}. Supported types: {supported_types}"
        )

    logger.debug(
        "Creating adapter instance",
        extra={
            "ats_type": ats_type,
            "source": source_config.identifier,
            "adapter_class": adapter_class.__name__,
        },
    )

    try:
        if adapter_class is SerpApiSearchAdapter:
            return SerpApiSearchAdapter(
                timeout=advanced_config.http_request_timeout,
                user_agent=advanced_config.user_agent,
                max_jobs=advanced_config.max_jobs_per_source,
                api_key=env_config.serpapi_api_key if env_config else None,
            )
        return adapter_class(
            timeout=advanced_config.http_request_timeout,
            user_agent=advanced_config.user_agent,
            max_jobs=advanced_config.max_jobs_per_source,
        )
    except Exception as e:
        raise AdapterConfigurationError(
            f"Failed to create {ats_type} adapter: {e}"
        ) from e
