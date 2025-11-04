"""ATS adapter implementations for different job posting platforms.

This module provides adapters for multiple ATS platforms:
- Greenhouse: greenhouse.GreenhouseAdapter
- Lever: lever.LeverAdapter
- Ashby: ashby.AshbyAdapter

Use the factory function to instantiate adapters:
    from app.adapters.factory import get_adapter
    adapter = get_adapter(source_config, advanced_config)
    jobs = adapter.fetch_jobs(source_config)

Or import directly:
    from app.adapters.greenhouse import GreenhouseAdapter
    from app.adapters.lever import LeverAdapter
    from app.adapters.ashby import AshbyAdapter

Exception handling:
    from app.adapters.exceptions import AdapterError, AdapterHTTPError, AdapterTimeoutError, AdapterResponseError

Base class:
    from app.adapters.base import BaseAdapter
"""

from .ashby import AshbyAdapter
from .base import BaseAdapter
from .exceptions import (
    AdapterConfigurationError,
    AdapterError,
    AdapterHTTPError,
    AdapterResponseError,
    AdapterTimeoutError,
)
from .factory import get_adapter
from .greenhouse import GreenhouseAdapter
from .lever import LeverAdapter

__all__ = [
    # Base and factory
    "BaseAdapter",
    "get_adapter",
    # Adapters
    "GreenhouseAdapter",
    "LeverAdapter",
    "AshbyAdapter",
    # Exceptions
    "AdapterError",
    "AdapterHTTPError",
    "AdapterTimeoutError",
    "AdapterResponseError",
    "AdapterConfigurationError",
]
