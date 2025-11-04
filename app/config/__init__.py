"""Configuration management module for Job Opportunity Scanner."""

from .environment import EnvironmentConfig, load_environment_config
from .exceptions import ConfigurationError
from .loader import load_config, validate_config_file
from .models import (
    ATSType,
    AdvancedConfig,
    AppConfig,
    EmailConfig,
    LogFormat,
    LogLevel,
    LoggingConfig,
    SearchCriteria,
    SourceConfig,
)

__all__ = [
    # Main loader functions
    "load_config",
    "validate_config_file",
    "load_environment_config",
    # Configuration models
    "AppConfig",
    "SourceConfig",
    "SearchCriteria",
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
