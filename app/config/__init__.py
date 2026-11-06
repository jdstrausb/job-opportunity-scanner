"""Configuration management module for Job Opportunity Scanner."""

from .environment import EnvironmentConfig, load_environment_config
from .exceptions import ConfigurationError
from .loader import load_config, validate_config_file
from .manager import ConfigManager, ConfigReloadResult, ConfigUpdatePayload
from .models import (
    ATSType,
    AdvancedConfig,
    AppConfig,
    EmailConfig,
    LogFormat,
    LogLevel,
    LoggingConfig,
    SearchCriteria,
    SearchProfile,
    SourceConfig,
)

__all__ = [
    # Main loader functions
    "load_config",
    "validate_config_file",
    "load_environment_config",
    # Round-trip editing and hot-reload
    "ConfigManager",
    "ConfigUpdatePayload",
    "ConfigReloadResult",
    # Configuration models
    "AppConfig",
    "SourceConfig",
    "SearchCriteria",
    "SearchProfile",
    "EmailConfig",
    "LoggingConfig",
    "AdvancedConfig",
    "EnvironmentConfig",
    # Enums
    "ATSType",
    "LogLevel",
    "LogFormat",
    # Exceptions
    "ConfigurationError",
]
