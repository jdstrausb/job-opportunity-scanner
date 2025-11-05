"""Keyword matching engine for filtering jobs against configured rules.

This module provides:
- MatchResult: Result of evaluating a job against SearchCriteria
- CandidateMatch: Coordination structure for matched jobs in the pipeline
- KeywordMatcher: Service to evaluate jobs against keyword rules
- Utility functions for building notification payloads and formatting results
"""

from .engine import KeywordMatcher
from .models import CandidateMatch, MatchResult
from .utils import build_notification_payload, build_rationale_dict, format_email_body

__all__ = [
    "KeywordMatcher",
    "MatchResult",
    "CandidateMatch",
    "build_notification_payload",
    "build_rationale_dict",
    "format_email_body",
]
