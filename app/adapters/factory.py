"""Factory function for instantiating ATS adapters."""

import logging

from app.config.models import AdvancedConfig, SourceConfig

from .ashby import AshbyAdapter
from .base import BaseAdapter
from .exceptions import AdapterConfigurationError
from .greenhouse import GreenhouseAdapter
from .lever import LeverAdapter

logger = logging.getLogger(__name__)


def get_adapter(source_config: SourceConfig, advanced_config: AdvancedConfig) -> BaseAdapter:
    """Factory function to instantiate the appropriate ATS adapter.

    Creates an adapter instance for the specified ATS type with configuration
    from advanced_config (timeout, user-agent, max_jobs).

    Args:
        source_config: Source configuration with ATS type and identifier
        advanced_config: Advanced configuration with timeout and user-agent settings

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
    adapter_map = {
        "greenhouse": GreenhouseAdapter,
        "lever": LeverAdapter,
        "ashby": AshbyAdapter,
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
        return adapter_class(
            timeout=advanced_config.http_request_timeout,
            user_agent=advanced_config.user_agent,
            max_jobs=advanced_config.max_jobs_per_source,
        )
    except Exception as e:
        raise AdapterConfigurationError(
            f"Failed to create {ats_type} adapter: {e}"
        ) from e
