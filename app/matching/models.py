"""Data models for the matching engine.

This module defines the data structures for keyword matching results and
coordination between normalization and downstream pipeline steps.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Set

from app.domain.models import Job

from app.normalization.models import MatchableText, NormalizationResult


@dataclass
class MatchResult:
    """Result of evaluating a job against SearchCriteria.

    Captures which terms matched where, whether it's an overall match,
    and provides snippets and formatted summary for notifications.

    Attributes:
        is_match: True if job matches all criteria (required terms, groups, no exclusions)
        matched_required_terms: Set of required terms that were found
        missing_required_terms: Set of required terms that were NOT found
        matched_keyword_groups: List of matched terms per group (indexed to original group order)
        missing_keyword_groups: List of group indices that had zero matches
        matched_exclude_terms: Set of exclude terms that were found (should be empty for pass)
        matched_fields: Dict mapping field name to set of matched terms found in that field
        snippets: List of text excerpts containing matched terms (for email)
        summary: Preformatted human-readable summary of matches
    """

    is_match: bool
    matched_required_terms: Set[str] = field(default_factory=set)
    missing_required_terms: Set[str] = field(default_factory=set)
    matched_keyword_groups: List[Set[str]] = field(default_factory=list)
    missing_keyword_groups: List[int] = field(default_factory=list)
    matched_exclude_terms: Set[str] = field(default_factory=set)
    matched_fields: Dict[str, Set[str]] = field(default_factory=dict)
    snippets: List[str] = field(default_factory=list)
    summary: str = ""

    def should_notify(self) -> bool:
        """Determine if a notification should be sent for this match.

        Returns True if it's an overall match and no exclude terms were found.

        Returns:
            True if job is a match and has no excluded terms
        """
        return self.is_match and not self.matched_exclude_terms

    @property
    def match_quality(self) -> str:
        """Return a description of match quality.

        Returns:
            "perfect" if all required terms and groups matched,
            "partial" if some groups missing,
            "excluded" if exclude terms found,
            "no-match" if required terms missing
        """
        if self.matched_exclude_terms:
            return "excluded"
        if self.missing_required_terms:
            return "no-match"
        if self.missing_keyword_groups:
            return "partial"
        if self.is_match:
            return "perfect"
        return "no-match"


@dataclass
class CandidateMatch:
    """Coordination structure for a matched job throughout the pipeline.

    Packages the normalization and matching results along with decisions
    about what persistence/notification actions should be taken.

    Attributes:
        normalization_result: Output from normalization (includes Job, change tracking)
        match_result: Output from keyword matching
        should_upsert: Whether to persist the job (new or content changed)
        should_notify: Whether to send a notification (is_match and no exclusions)
    """

    normalization_result: NormalizationResult
    match_result: MatchResult
    should_upsert: bool = True
    should_notify: bool = False

    def __post_init__(self):
        """Compute derived fields after initialization."""
        # should_upsert comes from normalization
        self.should_upsert = self.normalization_result.should_upsert

        # should_notify comes from match result
        self.should_notify = self.match_result.should_notify()

    @property
    def job(self) -> Job:
        """Convenience accessor for the normalized job."""
        return self.normalization_result.job

    @property
    def is_new(self) -> bool:
        """Convenience accessor for whether this is a new job."""
        return self.normalization_result.is_new

    @property
    def content_changed(self) -> bool:
        """Convenience accessor for whether content changed."""
        return self.normalization_result.content_changed
