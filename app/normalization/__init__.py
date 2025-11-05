"""Data normalization layer for converting raw job postings to unified domain models.

This module provides:
- NormalizationContext: Immutable context for normalization operations
- MatchableText: Normalized and original text variants for keyword matching
- NormalizationResult: Output of a normalization operation with change tracking
- JobNormalizer: Service to convert RawJob to Job with computed fields
"""

from .models import MatchableText, NormalizationContext, NormalizationResult
from .service import JobNormalizer

__all__ = [
    "JobNormalizer",
    "NormalizationContext",
    "NormalizationResult",
    "MatchableText",
]
