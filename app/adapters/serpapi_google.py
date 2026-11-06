"""SerpAPI Google search adapter with direct ATS JSON hydration."""

from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from app.config.models import SourceConfig
from app.domain.models import RawJob

from .ashby import AshbyAdapter
from .base import BaseAdapter
from .exceptions import (
    AdapterConfigurationError,
    AdapterError,
    AdapterHTTPError,
    AdapterResponseError,
)
from .greenhouse import GreenhouseAdapter
from .lever import LeverAdapter

logger = logging.getLogger(__name__)

GREENHOUSE_URL_RE = re.compile(
    r"(?:boards|job-boards)\.greenhouse\.io/([a-zA-Z0-9_-]+)/jobs/(\d+)"
)
LEVER_URL_RE = re.compile(r"jobs\.lever\.co/([a-zA-Z0-9_-]+)/([0-9a-fA-F-]{36})")
ASHBY_URL_RE = re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_.-]+)/([0-9a-fA-F-]{36})")

# Hydration failures that should degrade to the organic-result fallback.
_HYDRATION_ERRORS = (AdapterError, KeyError, ValueError, TypeError)


class SerpApiSearchAdapter(BaseAdapter):
    """Discover jobs through Google search results served by SerpAPI.

    The source ``identifier`` is the search query (e.g. a Google dork). Each
    organic result that links to a Greenhouse, Lever or Ashby posting is
    hydrated from that ATS's public JSON API; anything else (or any failed
    hydration) falls back to a RawJob built from the result title and snippet.

    ``options["num_results"]`` controls the number of results requested.
    """

    ADAPTER_NAME = "serpapi_google"
    SEARCH_URL = "https://serpapi.com/search.json"
    GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards"
    LEVER_API = "https://api.lever.co/v0/postings"

    def __init__(
        self,
        timeout: int = 30,
        user_agent: str = "JobOpportunityScanner/1.0",
        max_jobs: int = 1000,
        api_key: Optional[str] = None,
    ) -> None:
        super().__init__(timeout=timeout, user_agent=user_agent, max_jobs=max_jobs)
        self.api_key = (api_key or os.getenv("SERPAPI_API_KEY") or "").strip() or None
        # Reuse the per-ATS field mappings rather than duplicating them.
        self._greenhouse = GreenhouseAdapter(timeout=timeout, user_agent=user_agent, max_jobs=max_jobs)
        self._lever = LeverAdapter(timeout=timeout, user_agent=user_agent, max_jobs=max_jobs)
        self._ashby = AshbyAdapter(timeout=timeout, user_agent=user_agent, max_jobs=max_jobs)

    def fetch_jobs(self, source_config: SourceConfig) -> list[RawJob]:
        """Run the search and convert each organic result into a RawJob.

        Raises:
            AdapterConfigurationError: If no SerpAPI key is configured
            AdapterHTTPError / AdapterTimeoutError: If the search request fails
            AdapterResponseError: If the search payload is malformed
        """
        if not self.api_key:
            logger.error(
                "SERPAPI_API_KEY is required for serpapi_google sources",
                extra={"event": "adapter.serpapi.missing_api_key", "source": source_config.name},
            )
            raise AdapterConfigurationError("SERPAPI_API_KEY is required for serpapi_google sources")

        results = self._search(source_config)
        results = self._truncate_jobs(results, self.ADAPTER_NAME, source_config.name)

        ashby_cache: dict[str, Optional[dict[str, Any]]] = {}
        jobs: list[RawJob] = []
        seen: set[str] = set()

        for result in results:
            link = str(result.get("link") or "").strip()
            if not link:
                continue

            job = self._hydrate(link, source_config, ashby_cache) or self._fallback(result, link)
            if job is None or job.external_id in seen:
                continue
            seen.add(job.external_id)
            jobs.append(job)

        logger.info(
            "Completed SerpAPI discovery",
            extra={
                "adapter": self.ADAPTER_NAME,
                "source": source_config.name,
                "results": len(results),
                "count": len(jobs),
            },
        )
        return jobs

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _search(self, source_config: SourceConfig) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "engine": "google",
            "q": source_config.identifier,
            "api_key": self.api_key,
            "num": source_config.options.get("num_results", 20),
        }
        try:
            payload = self._make_request(self.SEARCH_URL, params=params)
        except AdapterError as e:
            # requests errors can echo the full URL, including the api_key query param.
            raise self._redact(e) from None

        if not isinstance(payload, dict):
            raise AdapterResponseError(f"Expected dict from SerpAPI, got {type(payload).__name__}")
        results = payload.get("organic_results") or []
        if not isinstance(results, list):
            raise AdapterResponseError("Expected 'organic_results' to be a list")
        return [r for r in results if isinstance(r, dict)]

    def _redact(self, error: AdapterError) -> AdapterError:
        message = str(error)
        if self.api_key and self.api_key in message:
            message = message.replace(self.api_key, "REDACTED")
        if isinstance(error, AdapterHTTPError):
            return AdapterHTTPError(message, status_code=error.status_code, url=self.SEARCH_URL)
        return type(error)(message)

    # ------------------------------------------------------------------
    # Hydration
    # ------------------------------------------------------------------

    def _hydrate(
        self,
        link: str,
        source_config: SourceConfig,
        ashby_cache: dict[str, Optional[dict[str, Any]]],
    ) -> Optional[RawJob]:
        """Return a fully hydrated RawJob for a recognised ATS link, else None."""
        try:
            if m := GREENHOUSE_URL_RE.search(link):
                return self._hydrate_greenhouse(m.group(1), m.group(2), source_config)
            if m := LEVER_URL_RE.search(link):
                return self._hydrate_lever(m.group(1), m.group(2), source_config)
            if m := ASHBY_URL_RE.search(link):
                return self._hydrate_ashby(m.group(1), m.group(2), source_config, ashby_cache)
        except _HYDRATION_ERRORS as e:
            logger.warning(
                "ATS hydration failed; falling back to search snippet",
                extra={"adapter": self.ADAPTER_NAME, "url": link, "error": str(e)},
            )
        return None

    @staticmethod
    def _as_company(source_config: SourceConfig, slug: str) -> SourceConfig:
        """Source config whose name is the ATS board slug, used as the company."""
        return source_config.model_copy(update={"name": slug, "identifier": slug})

    def _hydrate_greenhouse(self, board: str, job_id: str, source_config: SourceConfig) -> RawJob:
        data = self._make_request(f"{self.GREENHOUSE_API}/{board}/jobs/{job_id}")
        return self._greenhouse._transform_job(self._expect_dict(data), self._as_company(source_config, board))

    def _hydrate_lever(self, company: str, job_id: str, source_config: SourceConfig) -> RawJob:
        data = self._make_request(f"{self.LEVER_API}/{company}/{job_id}", params={"mode": "json"})
        return self._lever._transform_job(self._expect_dict(data), self._as_company(source_config, company))

    def _hydrate_ashby(
        self,
        org: str,
        job_id: str,
        source_config: SourceConfig,
        cache: dict[str, Optional[dict[str, Any]]],
    ) -> RawJob:
        if org not in cache:
            try:
                cache[org] = self._expect_dict(self._make_request(f"{AshbyAdapter.API_ENDPOINT}{org}"))
            except _HYDRATION_ERRORS:
                cache[org] = None  # don't retry a failing board for every hit
                raise
        board = cache[org]
        if board is None:
            raise AdapterResponseError(f"Ashby board unavailable for {org}")

        for job in board.get("jobs") or []:
            if isinstance(job, dict) and job.get("id") == job_id:
                return self._ashby._transform_job(job, self._as_company(source_config, org))
        raise AdapterResponseError(f"Ashby job {job_id} not found on board {org}")

    @staticmethod
    def _expect_dict(data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise AdapterResponseError(f"Expected JSON object, got {type(data).__name__}")
        return data

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------

    def _fallback(self, result: dict[str, Any], link: str) -> Optional[RawJob]:
        """Build a RawJob from the organic result's title and snippet."""
        title = str(result.get("title") or "").strip()
        snippet = str(result.get("snippet") or "").strip()
        if not title or not snippet:
            return None

        host = urlparse(link).hostname or "unknown"
        company = str(result.get("source") or "").strip() or host
        try:
            return RawJob(
                external_id=hashlib.sha256(link.encode("utf-8")).hexdigest(),
                title=title,
                company=company,
                location=None,
                description=snippet,
                url=link,
                posted_at=None,
                updated_at=None,
                is_remote=None,
                location_country=None,
            )
        except ValueError as e:
            logger.warning(
                "Skipping invalid fallback result",
                extra={"adapter": self.ADAPTER_NAME, "url": link, "error": str(e)},
            )
            return None
