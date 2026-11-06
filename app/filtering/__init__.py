"""Job filtering module.

This module provides filtering capabilities for jobs based on age and location
criteria, applied before keyword matching in the pipeline.
"""

from .age_filter import AgeFilter
from .domain_filter import DomainFilter
from .location_filter import LocationFilter
from .models import FilterContext, FilterResult
from .service import FilteringService

__all__ = [
    "AgeFilter",
    "DomainFilter",
    "LocationFilter",
    "FilterContext",
    "FilterResult",
    "FilteringService",
]
