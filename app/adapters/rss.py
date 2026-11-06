"""RSS 2.0 / Atom 1.0 feed adapter implementation."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

from app.config.models import SourceConfig
from app.domain.models import RawJob

from .base import BaseAdapter
from .exceptions import AdapterResponseError

logger = logging.getLogger(__name__)

ATOM_NS = "http://www.w3.org/2005/Atom"
DC_NS = "http://purl.org/dc/elements/1.1/"
_ATOM = f"{{{ATOM_NS}}}"


class RssAdapter(BaseAdapter):
    """Adapter for RSS 2.0 and Atom 1.0 job feeds.

    The source ``identifier`` is the feed URL. Optional
    ``options["default_location"]`` supplies a location for items that carry
    no location element.
    """

    ADAPTER_NAME = "rss"

    def fetch_jobs(self, source_config: SourceConfig) -> list[RawJob]:
        """Fetch and parse a feed into RawJob items.

        Raises:
            AdapterHTTPError / AdapterTimeoutError: On request failure
            AdapterResponseError: If the payload is not well-formed XML
        """
        url = source_config.identifier
        logger.info(
            "Fetching jobs from RSS feed",
            extra={"adapter": self.ADAPTER_NAME, "source": url},
        )

        text = self._make_text_request(url)

        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            raise AdapterResponseError(f"Failed to parse XML feed from {url}: {e}") from e

        if root.tag == f"{_ATOM}feed" or root.tag == f"{_ATOM}entry" or root.tag in ("entry", "feed"):
            is_atom = True
            entries = [root] if root.tag.endswith("entry") else list(root.iter(f"{_ATOM}entry")) or list(root.iter("entry"))
        else:
            is_atom = False
            entries = root.findall("./channel/item") or root.findall(".//item")

        entries = self._truncate_jobs(entries, self.ADAPTER_NAME, url)

        jobs: list[RawJob] = []
        for entry in entries:
            try:
                jobs.append(
                    self._transform_atom_entry(entry, source_config)
                    if is_atom
                    else self._transform_rss_item(entry, source_config)
                )
            except (ValueError, TypeError) as e:
                logger.warning(
                    "Failed to transform feed item",
                    extra={"adapter": self.ADAPTER_NAME, "source": url, "error": str(e)},
                )

        logger.info(
            "Successfully fetched jobs from RSS feed",
            extra={"adapter": self.ADAPTER_NAME, "source": url, "count": len(jobs)},
        )
        return jobs

    # ------------------------------------------------------------------
    # Transformers
    # ------------------------------------------------------------------

    def _transform_rss_item(self, item: ET.Element, source_config: SourceConfig) -> RawJob:
        link = self._text(item, "link")
        title = self._text(item, "title")
        external_id = self._text(item, "guid") or link
        description = self._text(item, "description") or self._text(
            item, "{http://purl.org/rss/1.0/modules/content/}encoded"
        )
        location = (
            self._text(item, "location")
            or self._text(item, "region")
            or self._default_location(source_config)
        )
        company = self._text(item, f"{{{DC_NS}}}creator") or source_config.name

        return RawJob(
            external_id=external_id or "",
            title=title or "",
            company=company,
            location=location,
            description=self._clean_html(description or "") or title or "",
            url=link or "",
            posted_at=self._parse_date(self._text(item, "pubDate") or self._text(item, f"{{{DC_NS}}}date")),
            updated_at=None,
            is_remote=None,
            location_country=None,
        )

    def _transform_atom_entry(self, entry: ET.Element, source_config: SourceConfig) -> RawJob:
        link = self._atom_link(entry)
        title = self._atom_text(entry, "title")
        external_id = self._atom_text(entry, "id") or link
        description = self._atom_text(entry, "content") or self._atom_text(entry, "summary")
        author = entry.find(f"{_ATOM}author/{_ATOM}name")
        if author is None:
            author = entry.find("author/name")
        company = (author.text or "").strip() if author is not None and author.text else ""

        updated = self._parse_date(self._atom_text(entry, "updated"))
        published = self._parse_date(self._atom_text(entry, "published"))

        return RawJob(
            external_id=external_id or "",
            title=title or "",
            company=company or source_config.name,
            location=self._atom_text(entry, "location") or self._default_location(source_config),
            description=self._clean_html(description or "") or title or "",
            url=link or "",
            posted_at=published,
            updated_at=updated,
            is_remote=None,
            location_country=None,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _text(element: ET.Element, tag: str) -> Optional[str]:
        child = element.find(tag)
        if child is not None and child.text and child.text.strip():
            return child.text.strip()
        return None

    def _atom_text(self, element: ET.Element, name: str) -> Optional[str]:
        """Read child text from an Atom entry, tolerating a missing namespace."""
        return self._text(element, f"{_ATOM}{name}") or self._text(element, name)

    @staticmethod
    def _atom_link(entry: ET.Element) -> Optional[str]:
        """Prefer rel=alternate (or rel-less) link; fall back to the first link."""
        links = entry.findall(f"{_ATOM}link") or entry.findall("link")
        for link in links:
            if link.get("rel", "alternate") == "alternate" and link.get("href"):
                return link.get("href", "").strip()
        for link in links:
            if link.get("href"):
                return link.get("href", "").strip()
        return None

    @staticmethod
    def _default_location(source_config: SourceConfig) -> Optional[str]:
        value = source_config.options.get("default_location")
        return str(value) if value else None

    def _parse_date(self, value: Optional[str]) -> Optional[datetime]:
        """Parse RFC-2822 (RSS) first, then ISO-8601 (Atom); always UTC-aware."""
        if not value:
            return None
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (TypeError, ValueError, IndexError):
            return self._parse_timestamp(value)
