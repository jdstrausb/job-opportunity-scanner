"""Keyword matching engine for evaluating jobs against search criteria.

This module implements the matching logic that:
1. Evaluates normalized jobs against SearchCriteria rules
2. Tracks which terms/groups matched and where
3. Provides snippets and formatted reasoning for notifications
4. Supports boundary matching and contextual matching for false-positive reduction
"""

import logging
import re
from typing import Dict, List, Optional, Pattern, Sequence, Set, Union

from app.config.models import SearchCriteria, SearchProfile
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
    - Support boundary matching for terms that need word boundaries (e.g., "rails" vs "guardrails")
    - Support contextual matching for terms that require context (e.g., "rails" requires "ruby")
    - Track which terms matched in which fields
    - Generate snippets for notifications
    - Provide formatted summary of matches
    """

    def __init__(
        self,
        search_criteria: Union[SearchCriteria, Sequence[SearchProfile]],
        logger_instance: Optional[logging.Logger] = None,
    ):
        """Initialize KeywordMatcher.

        Args:
            search_criteria: A single SearchCriteria/SearchProfile (legacy single-track
                usage) or a sequence of SearchProfile tracks. A bare SearchCriteria is
                wrapped into a profile named "default".
            logger_instance: Optional logger instance (defaults to module logger)
        """
        self.profiles: List[SearchProfile] = self._normalize_profiles(search_criteria)
        # Backwards-compatible attribute: the primary (first) profile
        self.search_criteria: SearchProfile = self.profiles[0]
        self.logger = logger_instance or logger

        # Pre-compile boundary term patterns (shared across profiles)
        self._boundary_patterns: Dict[str, Pattern[str]] = {}
        for profile in self.profiles:
            for term in profile.boundary_terms:
                if term not in self._boundary_patterns:
                    self._boundary_patterns[term] = re.compile(
                        rf"\b{re.escape(term)}\b", re.IGNORECASE
                    )

    @staticmethod
    def _normalize_profiles(
        search_criteria: Union[SearchCriteria, Sequence[SearchProfile]],
    ) -> List[SearchProfile]:
        """Normalize constructor input into a non-empty list of SearchProfile."""
        if isinstance(search_criteria, SearchProfile):
            return [search_criteria]
        if isinstance(search_criteria, SearchCriteria):
            return [SearchProfile(name="default", **search_criteria.model_dump())]
        profiles = list(search_criteria)
        if not profiles:
            raise ValueError("KeywordMatcher requires at least one search profile")
        return profiles

    def evaluate(self, job: Job, matchable_text: MatchableText) -> MatchResult:
        """Evaluate a job against every search profile.

        Each profile is evaluated independently (required terms, groups, exclusions,
        boundary/contextual rules, then affinity scoring). When one or more profiles
        match, the highest-scoring profile becomes ``primary_profile`` and all
        matching profile names are listed in ``matched_profiles`` by score descending.
        The returned details (snippets, fields, score) are those of the winning
        profile. When no profile matches, the first profile's non-match details are
        returned.

        Args:
            job: Job domain model to evaluate
            matchable_text: MatchableText with normalized variants

        Returns:
            MatchResult with match decision and details
        """
        field_index = {
            "title": matchable_text.title_normalized,
            "description": matchable_text.description_normalized,
            "location": matchable_text.location_normalized,
        }

        results = [
            (profile, self._evaluate_profile(profile, field_index, matchable_text))
            for profile in self.profiles
        ]
        matched = [(p, r) for p, r in results if r.is_match]

        if not matched:
            result = results[0][1]
            self.logger.debug(
                f"Job did not match: {job.job_key}",
                extra={
                    "job_key": job.job_key,
                    "company": job.company,
                    "reason": self._non_match_reason(result),
                },
            )
            return result

        # sorted() is stable, so ties keep configured profile order
        matched.sort(key=lambda pr: pr[1].affinity_score, reverse=True)
        winner_profile, winner = matched[0]
        winner.primary_profile = winner_profile.name
        winner.matched_profiles = [p.name for p, _ in matched]

        self.logger.info(
            f"Job matched: {job.job_key}",
            extra={
                "job_key": job.job_key,
                "company": job.company,
                "primary_profile": winner.primary_profile,
                "matched_profiles": winner.matched_profiles,
                "affinity_score": winner.affinity_score,
                "required_matched": len(winner.matched_required_terms),
                "required_total": len(winner_profile.required_terms),
                "groups_matched": len([g for g in winner.matched_keyword_groups if g]),
                "groups_total": len(winner_profile.keyword_groups),
                "boundary_matches": winner.boundary_matches,
                "context_matches": winner.context_matches,
            },
        )
        return winner

    @staticmethod
    def _non_match_reason(result: MatchResult) -> Optional[str]:
        if result.missing_required_terms:
            return f"missing_required_terms: {sorted(result.missing_required_terms)}"
        if result.missing_keyword_groups:
            return f"missing_keyword_groups: {result.missing_keyword_groups}"
        if result.matched_exclude_terms:
            return f"matched_exclude_terms: {sorted(result.matched_exclude_terms)}"
        if result.is_match is False and result.score_breakdown:
            return f"below_min_affinity_score: {result.affinity_score}"
        return None

    def _evaluate_profile(
        self,
        profile: SearchProfile,
        field_index: Dict[str, str],
        matchable_text: MatchableText,
    ) -> MatchResult:
        """Evaluate one profile: boolean gates first, then affinity scoring."""
        matched_required_terms: Set[str] = set()
        missing_required_terms: Set[str] = set()
        matched_keyword_groups: List[Set[str]] = []
        missing_keyword_groups: List[int] = []
        matched_exclude_terms: Set[str] = set()
        matched_fields: Dict[str, Set[str]] = {"title": set(), "description": set(), "location": set()}
        boundary_matches: Dict[str, bool] = {}
        context_matches: Dict[str, bool] = {}

        for term in profile.required_terms:
            if self._check_term_match(profile, term, field_index, boundary_matches, context_matches):
                matched_required_terms.add(term)
                self._record_term_matches(term, field_index, matched_fields)
            else:
                missing_required_terms.add(term)

        for group_idx, group in enumerate(profile.keyword_groups):
            matched_in_group: Set[str] = set()
            for term in group:
                if self._check_term_match(profile, term, field_index, boundary_matches, context_matches):
                    matched_in_group.add(term)
                    self._record_term_matches(term, field_index, matched_fields)
            matched_keyword_groups.append(matched_in_group)
            if not matched_in_group:
                missing_keyword_groups.append(group_idx)

        # Exclude terms always use whole-word matching
        for term in profile.exclude_terms:
            if self._term_matches_any_field(term, field_index, whole_word=True):
                matched_exclude_terms.add(term)

        gates_pass = (
            not missing_required_terms and not missing_keyword_groups and not matched_exclude_terms
        )

        # Affinity scoring: each unique matched term is scored once per profile
        score_breakdown: Dict[str, float] = {}
        affinity_score = 0.0
        if gates_pass:
            title_text = field_index["title"]
            scored_terms: Set[str] = set(matched_required_terms)
            for group_matches in matched_keyword_groups:
                scored_terms |= group_matches
            for term in profile.affinity_weights:
                if term in scored_terms:
                    continue
                if self._check_term_match(profile, term, field_index, boundary_matches, context_matches):
                    scored_terms.add(term)
                    self._record_term_matches(term, field_index, matched_fields)

            for term in sorted(scored_terms):
                weight = profile.affinity_weights.get(term, 1.0)
                if self._term_in_title(profile, term, title_text):
                    weight *= profile.title_boost_multiplier
                score_breakdown[term] = round(weight, 4)
            affinity_score = round(sum(score_breakdown.values()), 2)

        below_threshold = gates_pass and affinity_score < profile.min_affinity_score
        is_match = gates_pass and not below_threshold

        all_matched_terms = list(matched_required_terms)
        for group_matches in matched_keyword_groups:
            all_matched_terms.extend(group_matches)
        all_matched_terms.extend(t for t in score_breakdown if t not in all_matched_terms)

        snippets = extract_snippets_with_keywords(
            matchable_text.description_original, all_matched_terms, context_chars=100
        )

        summary_parts: List[str] = []
        if (
            matched_fields["location"]
            and not matched_fields["title"]
            and not matched_fields["description"]
        ):
            summary_parts.append(f"Location matched: {', '.join(sorted(matched_fields['location']))}")
        boundary_used = [t for t, used in boundary_matches.items() if used]
        if boundary_used:
            summary_parts.append(f"Boundary matched: {', '.join(boundary_used)}")
        context_used = [t for t, passed in context_matches.items() if passed]
        if context_used:
            summary_parts.append(f"Context verified: {', '.join(context_used)}")
        summary = format_matched_terms(
            list(matched_required_terms),
            [sorted(g) for g in matched_keyword_groups],
            list(matched_exclude_terms),
        )
        if summary:
            summary_parts.append(summary)
        if is_match and not (len(self.profiles) == 1 and profile.name == "default"):
            summary_parts.append(f"Profile: {profile.name} (affinity score {affinity_score})")

        return MatchResult(
            is_match=is_match,
            matched_required_terms=matched_required_terms,
            missing_required_terms=missing_required_terms,
            matched_keyword_groups=matched_keyword_groups,
            missing_keyword_groups=missing_keyword_groups,
            matched_exclude_terms=matched_exclude_terms,
            matched_fields=matched_fields,
            snippets=snippets,
            summary="\n".join(summary_parts) if summary_parts else "No specific match criteria",
            boundary_matches=boundary_matches,
            context_matches=context_matches,
            affinity_score=affinity_score,
            score_breakdown=score_breakdown,
        )

    def _term_in_title(self, profile: SearchProfile, term: str, title_text: str) -> bool:
        """Check whether a term appears in the title, honoring boundary rules."""
        if term in profile.boundary_terms:
            return bool(self._boundary_patterns[term].search(title_text))
        return self._term_in_text(term, title_text, whole_word=False)

    def _check_term_match(
        self,
        profile: SearchProfile,
        term: str,
        field_index: Dict[str, str],
        boundary_matches: Dict[str, bool],
        context_matches: Dict[str, bool],
    ) -> bool:
        """Check if a term matches, applying the profile's boundary and context rules.

        Args:
            profile: Profile whose boundary/contextual rules apply
            term: Term to check
            field_index: Dict of field names to normalized text
            boundary_matches: Dict to record boundary match usage
            context_matches: Dict to record context match results

        Returns:
            True if term matches (with all applicable rules satisfied)
        """
        is_boundary_term = term in profile.boundary_terms
        context_terms = profile.contextual_terms.get(term, [])

        if is_boundary_term:
            if not self._boundary_term_matches(term, field_index):
                return False
            boundary_matches[term] = True
        elif not self._term_matches_any_field(term, field_index, whole_word=False):
            return False

        if context_terms:
            all_text = " ".join(field_index.values()).lower()
            context_found = any(c.lower() in all_text for c in context_terms)
            context_matches[term] = context_found
            if not context_found:
                return False

        return True

    def _boundary_term_matches(self, term: str, field_index: Dict[str, str]) -> bool:
        """Check if term matches with word boundaries in any field.

        Args:
            term: Term to match (should be in boundary_terms)
            field_index: Dict of field names to normalized text

        Returns:
            True if term matches with word boundaries
        """
        pattern = self._boundary_patterns.get(term)
        if not pattern:
            # Fallback: create pattern if not pre-compiled
            pattern = re.compile(rf'\b{re.escape(term)}\b', re.IGNORECASE)

        for field_text in field_index.values():
            if field_text and pattern.search(field_text):
                return True
        return False

    @staticmethod
    def _term_matches_any_field(
        term: str, field_index: Dict[str, str], *, whole_word: bool = False
    ) -> bool:
        """Check if a term (substring or whole word) appears in any field.

        Args:
            term: Normalized term to search for
            field_index: Dict of field names to normalized text
            whole_word: When True, require word-boundary matches for single-word terms

        Returns:
            True if term found in any field
        """
        for field_text in field_index.values():
            if KeywordMatcher._term_in_text(term, field_text, whole_word=whole_word):
                return True
        return False

    @staticmethod
    def _term_in_text(term: str, field_text: str, *, whole_word: bool) -> bool:
        """Determine if the term exists within the provided text."""
        if not field_text:
            return False

        if whole_word and " " not in term:
            pattern = re.compile(rf"\b{re.escape(term)}\b")
            return bool(pattern.search(field_text))

        return term in field_text

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
