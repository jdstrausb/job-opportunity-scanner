"""Secret-hygiene tests for the ATS identifier scraper utility."""

import importlib.util
import re
from pathlib import Path
from unittest.mock import patch

SCRAPER_PATH = Path(__file__).resolve().parent.parent / "ats_identifier_scraper" / "scraper.py"


def _load_scraper():
    spec = importlib.util.spec_from_file_location("ats_scraper_under_test", SCRAPER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scraper_contains_no_hardcoded_keys():
    """No 64-character hex literal (SerpAPI key shape) may appear in the scraper."""
    source = SCRAPER_PATH.read_text()
    assert re.search(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])", source) is None


def test_get_career_page_url_without_api_key_fails_gracefully(monkeypatch):
    """Missing SERPAPI_API_KEY returns None and makes no search request."""
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    scraper = _load_scraper()

    with patch.object(scraper, "GoogleSearch") as mock_search:
        assert scraper.get_career_page_url("Acme Corp") is None

    mock_search.assert_not_called()


def test_get_career_page_url_blank_api_key_fails_gracefully(monkeypatch):
    """A whitespace-only key is treated as missing."""
    monkeypatch.setenv("SERPAPI_API_KEY", "   ")
    scraper = _load_scraper()

    with patch.object(scraper, "GoogleSearch") as mock_search:
        assert scraper.get_career_page_url("Acme Corp") is None

    mock_search.assert_not_called()


def test_get_career_page_url_uses_env_key(monkeypatch):
    """A configured key is passed to the search client."""
    monkeypatch.setenv("SERPAPI_API_KEY", " abc123 ")
    scraper = _load_scraper()

    with patch.object(scraper, "GoogleSearch") as mock_search:
        mock_search.return_value.get_dict.return_value = {
            "organic_results": [{"link": "https://acme.example/careers"}]
        }
        assert scraper.get_career_page_url("Acme Corp") == "https://acme.example/careers"

    assert mock_search.call_args.args[0]["api_key"] == "abc123"
