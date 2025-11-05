"""Keyword matching engine for evaluating jobs against search criteria.

This module implements the matching logic that:
1. Evaluates normalized jobs against SearchCriteria rules
2. Tracks which terms/groups matched and where
3. Provides snippets and formatted reasoning for notifications
"""

import logging
from typing import Dict, List, Set

from app.config.models import SearchCriteria
from app.domain.models import Job
from app.normalization.models import MatchableText
from app.utils.highlighting import extract_snippets_with_keywords, format_matched_terms

from .models import MatchResult

logger = logging.getLogger(__name__)


class KeywordMatcher:
    """Evaluates jobs against keyword matching criteria.

    Responsibilities:
    - Match required terms (AND logic)
    - Match keyword groups (at least one from each group)
    - Detect excluded terms (fail if found)
    - Track which terms matched in which fields
    - Generate snippets for notifications
    - Provide formatted summary of matches
    """

    def __init__(self, search_criteria: SearchCriteria, logger_instance: logging.Logger = None):
        """Initialize KeywordMatcher.

        Args:
            search_criteria: SearchCriteria containing keyword rules
            logger_instance: Optional logger instance (defaults to module logger)
        """
        self.search_criteria = search_criteria
        self.logger = logger_instance or logger

    def evaluate(self, job: Job, matchable_text: MatchableText) -> MatchResult:
        """Evaluate a job against search criteria.

        Algorithm:
        1. Build field_index for quick membership checks
        2. Check required terms (all must match)
        3. Check keyword groups (at least one from each group)
        4. Check exclude terms (fail if any found)
        5. Compute overall is_match decision
        6. Generate snippets and summary
        7. Return MatchResult

        Args:
            job: Job domain model to evaluate
            matchable_text: MatchableText with normalized variants

        Returns:
            MatchResult with match decision and details
        """
        # Step 1: Build field index for quick lookups
        field_index = {
            "title": matchable_text.title_normalized,
            "description": matchable_text.description_normalized,
            "location": matchable_text.location_normalized,
        }

        # Initialize result tracking
        matched_required_terms: Set[str] = set()
        missing_required_terms: Set[str] = set()
        matched_keyword_groups: List[Set[str]] = []
        missing_keyword_groups: List[int] = []
        matched_exclude_terms: Set[str] = set()
        matched_fields: Dict[str, Set[str]] = {
            "title": set(),
            "description": set(),
            "location": set(),
        }

        # Step 2: Check required terms (substring match on normalized strings)
        for term in self.search_criteria.required_terms:
            if self._term_matches_any_field(term, field_index):
                matched_required_terms.add(term)
                self._record_term_matches(term, field_index, matched_fields)
            else:
                missing_required_terms.add(term)

        # Step 3: Check keyword groups
        for group_idx, group in enumerate(self.search_criteria.keyword_groups):
            matched_in_group = set()
            for term in group:
                if self._term_matches_any_field(term, field_index):
                    matched_in_group.add(term)
                    self._record_term_matches(term, field_index, matched_fields)

            matched_keyword_groups.append(matched_in_group)
            if not matched_in_group:
                missing_keyword_groups.append(group_idx)

        # Step 4: Check exclude terms
        for term in self.search_criteria.exclude_terms:
            if self._term_matches_any_field(term, field_index):
                matched_exclude_terms.add(term)

        # Step 5: Compute overall match decision
        # Match if: all required terms present, every group has ≥1 match, no exclude terms
        is_match = (
            not missing_required_terms
            and not missing_keyword_groups
            and not matched_exclude_terms
        )

        # Step 6: Generate snippets and summary
        all_matched_terms = list(matched_required_terms)
        for group_matches in matched_keyword_groups:
            all_matched_terms.extend(group_matches)

        snippets = extract_snippets_with_keywords(
            matchable_text.description_original, all_matched_terms, context_chars=100
        )

        # Build formatted summary
        summary_parts = []

        # Add location-specific note if matches only in location
        if (
            matched_fields["location"]
            and not matched_fields["title"]
            and not matched_fields["description"]
        ):
            location_terms = ", ".join(sorted(matched_fields["location"]))
            summary_parts.append(f"Location matched: {location_terms}")

        # Add general match summary
        summary = format_matched_terms(
            list(matched_required_terms),
            matched_keyword_groups,
            list(matched_exclude_terms),
        )
        if summary:
            summary_parts.append(summary)

        final_summary = "\n".join(summary_parts) if summary_parts else "No specific match criteria"

        # Step 7: Log match decision
        if is_match:
            self.logger.info(
                f"Job matched: {job.job_key}",
                extra={
                    "job_key": job.job_key,
                    "company": job.company,
                    "required_matched": len(matched_required_terms),
                    "required_total": len(self.search_criteria.required_terms),
                    "groups_matched": len([g for g in matched_keyword_groups if g]),
                    "groups_total": len(self.search_criteria.keyword_groups),
                },
            )
        else:
            reason = None
            if missing_required_terms:
                reason = f"missing_required_terms: {sorted(missing_required_terms)}"
            elif missing_keyword_groups:
                reason = f"missing_keyword_groups: {missing_keyword_groups}"
            elif matched_exclude_terms:
                reason = f"matched_exclude_terms: {sorted(matched_exclude_terms)}"

            self.logger.debug(
                f"Job did not match: {job.job_key}",
                extra={
                    "job_key": job.job_key,
                    "company": job.company,
                    "reason": reason,
                },
            )

        # Step 8: Return MatchResult
        return MatchResult(
            is_match=is_match,
            matched_required_terms=matched_required_terms,
            missing_required_terms=missing_required_terms,
            matched_keyword_groups=matched_keyword_groups,
            missing_keyword_groups=missing_keyword_groups,
            matched_exclude_terms=matched_exclude_terms,
            matched_fields=matched_fields,
            snippets=snippets,
            summary=final_summary,
        )

    @staticmethod
    def _term_matches_any_field(term: str, field_index: Dict[str, str]) -> bool:
        """Check if a term (substring) appears in any field.

        Uses simple substring matching on normalized (lowercase, punctuation-stripped) text.

        Args:
            term: Normalized term to search for
            field_index: Dict of field names to normalized text

        Returns:
            True if term found in any field
        """
        for field_text in field_index.values():
            if term in field_text:
                return True
        return False

    @staticmethod
    def _record_term_matches(
        term: str, field_index: Dict[str, str], matched_fields: Dict[str, Set[str]]
    ) -> None:
        """Record which fields a term was found in.

        Updates matched_fields dict to track field hits for reporting.

        Args:
            term: Term that matched
            field_index: Dict of field names to normalized text
            matched_fields: Dict to accumulate field hits
        """
        for field_name, field_text in field_index.items():
            if term in field_text:
                matched_fields[field_name].add(term)
