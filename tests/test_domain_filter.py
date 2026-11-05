"""Unit tests for the DomainFilter.

Tests for:
- Company blocklist (case-insensitive exact match)
- Industry/domain term blocking with word boundaries
- URL hostname blocking
- domain_allow_terms override
- FilteringService integration (stage 4, short-circuit)
"""

from datetime import datetime, timezone

import pytest

from app.config.models import FilteringRules
from app.domain.models import Job
from app.filtering.domain_filter import DomainFilter
from app.filtering.models import FilterContext
from app.filtering.service import FilteringService


def make_context(**rule_overrides) -> FilterContext:
    return FilterContext(
        filtering_rules=FilteringRules(**rule_overrides),
        scan_timestamp=datetime.now(timezone.utc),
        run_id="test-run-001",
    )


@pytest.fixture
def base_job():
    now = datetime.now(timezone.utc)
    return Job(
        job_key="test-job-001",
        source_type="greenhouse",
        source_identifier="test-company",
        external_id="12345",
        title="Software Engineer",
        company="Test Corp",
        location="Remote",
        description="Build great backend services.",
        url="https://boards.example.com/job/12345",
        posted_at=now,
        updated_at=now,
        first_seen_at=now,
        last_seen_at=now,
        content_hash="abc123",
    )


class TestDomainFilter:
    def test_blocks_configured_company_case_insensitive(self, base_job):
        ctx = make_context(blocked_companies=["Crossover", "  BairesDev "])
        domain_filter = DomainFilter()

        base_job.company = "CROSSOVER"
        result = domain_filter.evaluate(base_job, ctx)
        assert result.should_process is False
        assert result.filter_reason == "domain_filter_company_blocked"
        assert result.domain_blocked is True
        assert result.filter_metadata["blocked_company"] == "crossover"

        base_job.company = "bairesdev"
        result = domain_filter.evaluate(base_job, ctx)
        assert result.should_process is False
        assert result.domain_blocked is True

        base_job.company = "Crossover Health Labs"
        assert domain_filter.evaluate(base_job, ctx).should_process is True

    def test_blocks_configured_domain_terms_word_boundary(self, base_job):
        ctx = make_context(blocked_domains=["crypto", "sports betting", "bet"])
        domain_filter = DomainFilter()

        base_job.description = "Join our Crypto exchange team."
        result = domain_filter.evaluate(base_job, ctx)
        assert result.should_process is False
        assert result.filter_reason == "domain_filter_domain_blocked"
        assert result.domain_blocked is True
        assert result.filter_metadata["blocked_domain"] == "crypto"

        base_job.description = "We power Sports Betting platforms."
        result = domain_filter.evaluate(base_job, ctx)
        assert result.should_process is False
        assert result.filter_metadata["blocked_domain"] == "sports betting"

        base_job.title = "Engineer"
        base_job.description = "We build better tools for a better world."
        result = domain_filter.evaluate(base_job, ctx)
        assert result.should_process is True
        assert result.domain_blocked is False

    def test_blocks_matching_url_hostname(self, base_job):
        ctx = make_context(blocked_domains=["badcorp.com"])
        domain_filter = DomainFilter()

        base_job.url = "https://jobs.BadCorp.com/openings/1"
        result = domain_filter.evaluate(base_job, ctx)
        assert result.should_process is False
        assert result.filter_reason == "domain_filter_domain_blocked"
        assert result.filter_metadata["blocked_domain"] == "badcorp.com"

        base_job.url = "https://notbadcorp.com/openings/1"
        assert domain_filter.evaluate(base_job, ctx).should_process is True

        base_job.url = "https://example.com/careers?ref=badcorp.com"
        assert domain_filter.evaluate(base_job, ctx).should_process is True

    def test_domain_allow_terms_override_blocked_domain(self, base_job):
        ctx = make_context(
            blocked_domains=["crypto"],
            domain_allow_terms=["cryptography"],
        )
        domain_filter = DomainFilter()

        base_job.description = "Work on applied cryptography and crypto wallets."
        result = domain_filter.evaluate(base_job, ctx)
        assert result.should_process is True
        assert result.domain_blocked is False

        base_job.description = "Work on crypto wallets."
        assert domain_filter.evaluate(base_job, ctx).should_process is False

    def test_disabled_filter_passes(self, base_job):
        ctx = make_context(
            enable_domain_filter=False,
            blocked_companies=["test corp"],
        )
        result = DomainFilter().evaluate(base_job, ctx)
        assert result.should_process is True
        assert result.filter_metadata == {"domain_filter": "disabled"}


class TestFilteringServiceWithDomainFilter:
    def test_filtering_service_short_circuits_on_domain_filter(self, base_job):
        ctx = make_context(blocked_companies=["Test Corp"])
        service = FilteringService()
        assert isinstance(service.domain_filter, DomainFilter)

        result = service.evaluate_job(base_job, ctx)
        assert result.should_process is False
        assert result.filter_reason == "domain_filter_company_blocked"
        assert result.domain_blocked is True
        # Metadata from prior stages merged in
        assert result.filter_metadata["role_filter"] == "passed"
        assert result.is_remote_eligible is True

    def test_filtering_service_passes_when_not_blocked(self, base_job):
        ctx = make_context(blocked_companies=["Other Corp"])
        result = FilteringService().evaluate_job(base_job, ctx)
        assert result.should_process is True
        assert result.domain_blocked is False
        assert result.filter_metadata["domain_filter"] == "passed"
