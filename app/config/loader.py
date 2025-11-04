"""Configuration loader for Job Opportunity Scanner."""

from pathlib import Path
from typing import Optional

import yaml
from pydantic import ValidationError

from .environment import EnvironmentConfig, load_environment_config
from .exceptions import ConfigurationError
from .models import AppConfig
from .validators import check_for_warnings, emit_warnings


def load_config(config_path: Optional[Path] = None) -> tuple[AppConfig, EnvironmentConfig]:
    """
    Load and validate configuration from YAML file and environment variables.

    Implements fallback logic for config file location:
    1. Use provided config_path if given
    2. Try config.yaml in current directory
    3. Try ./config/config.yaml
    4. Fail with helpful error message

    Args:
        config_path: Optional path to configuration file

    Returns:
        Tuple of (AppConfig, EnvironmentConfig) with validated configuration

    Raises:
        ConfigurationError: If configuration is invalid or file not found
    """
    # Determine config file path with fallback logic
    config_file = _find_config_file(config_path)

    # Load YAML file
    try:
        with open(config_file, "r") as f:
            config_dict = yaml.safe_load(f)
    except FileNotFoundError:
        raise ConfigurationError(
            f"Configuration file not found: {config_file}",
            suggestions=[
                "Copy config.example.yaml to config.yaml",
                "Create a config.yaml file with your settings",
                f"Ensure {config_file} exists and is readable",
            ],
        )
    except yaml.YAMLError as e:
        raise ConfigurationError(
            f"Failed to parse YAML configuration: {e}",
            suggestions=[
                "Check YAML syntax in your config file",
                "Ensure proper indentation (use spaces, not tabs)",
                "Validate your YAML file with a YAML validator",
            ],
        )
    except Exception as e:
        raise ConfigurationError(
            f"Failed to read configuration file: {e}",
            suggestions=[
                f"Ensure {config_file} is readable",
                "Check file permissions",
            ],
        )

    # Check if config is empty
    if not config_dict:
        raise ConfigurationError(
            "Configuration file is empty",
            suggestions=[
                "Copy config.example.yaml to config.yaml",
                "Add configuration settings to your config file",
            ],
        )

    # Check for warnings before validation
    warnings = check_for_warnings(config_dict)
    if warnings:
        emit_warnings(warnings)

    # Validate configuration with Pydantic
    try:
        app_config = AppConfig.model_validate(config_dict)
    except ValidationError as e:
        # Convert Pydantic validation errors to ConfigurationError
        errors = []
        for error in e.errors():
            field_path = " -> ".join(str(loc) for loc in error["loc"])
            error_msg = error["msg"]
            error_type = error["type"]

            # Format user-friendly error messages
            if error_type == "missing":
                errors.append(f"Missing required field: {field_path}")
            elif error_type in ["string_type", "int_type", "bool_type", "list_type"]:
                expected_type = error_type.replace("_type", "")
                errors.append(
                    f"Invalid type for '{field_path}': expected {expected_type}, got {error.get('input')}"
                )
            elif "enum" in error_type:
                errors.append(
                    f"Invalid value for '{field_path}': {error_msg}"
                )
            else:
                errors.append(f"{field_path}: {error_msg}")

        raise ConfigurationError(
            "Configuration validation failed",
            errors=errors,
            suggestions=[
                "Review config.example.yaml for correct format",
                "Check that all required fields are present",
                "Verify field types match the expected schema",
            ],
        )
    except Exception as e:
        raise ConfigurationError(
            f"Unexpected error during configuration validation: {e}",
            suggestions=[
                "Check your configuration file for errors",
                "Ensure all values are properly formatted",
            ],
        )

    # Load and validate environment variables
    try:
        env_config = load_environment_config()
    except ConfigurationError:
        # Re-raise ConfigurationError as-is
        raise
    except Exception as e:
        raise ConfigurationError(
            f"Failed to load environment configuration: {e}",
            suggestions=[
                "Copy .env.example to .env and fill in your credentials",
                "Ensure all required environment variables are set",
            ],
        )

    return app_config, env_config


def _find_config_file(config_path: Optional[Path] = None) -> Path:
    """
    Find configuration file using fallback logic.

    Args:
        config_path: Optional explicit path to config file

    Returns:
        Path to configuration file

    Raises:
        ConfigurationError: If no config file is found
    """
    # If explicit path provided, use it
    if config_path:
        if not config_path.exists():
            raise ConfigurationError(
                f"Specified configuration file not found: {config_path}",
                suggestions=[
                    f"Ensure {config_path} exists",
                    "Check the path and try again",
                ],
            )
        return config_path

    # Try default locations
    candidates = [
        Path("config.yaml"),
        Path("config") / "config.yaml",
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    # No config file found
    raise ConfigurationError(
        "Configuration file not found",
        errors=[
            "Tried: config.yaml",
            "Tried: config/config.yaml",
        ],
        suggestions=[
            "Copy config.example.yaml to config.yaml",
            "Create a config.yaml file in the current directory",
            "Use --config flag to specify a custom location",
        ],
    )


def validate_config_file(config_path: Path) -> bool:
    """
    Validate a configuration file without loading environment variables.

    Useful for testing or pre-deployment validation.

    Args:
        config_path: Path to configuration file

    Returns:
        True if valid, False otherwise (errors printed to stderr)
    """
    try:
        # Load YAML
        with open(config_path, "r") as f:
            config_dict = yaml.safe_load(f)

        # Validate with Pydantic
        AppConfig.model_validate(config_dict)
        print(f"✓ Configuration file {config_path} is valid")
        return True

    except ConfigurationError as e:
        print(f"✗ Configuration validation failed:\n{e}")
        return False
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        return False
