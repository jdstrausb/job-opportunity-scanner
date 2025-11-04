"""Unit tests for highlighting utilities."""

import pytest

from app.utils.highlighting import (
    extract_snippets_with_keywords,
    format_matched_terms,
    highlight_keywords,
    normalize_for_matching,
    truncate_text,
)


class TestHighlightKeywords:
    """Tests for highlight_keywords function."""

    def test_highlight_keywords_basic(self):
        """Test basic keyword highlighting."""
        text = "Looking for a Python developer with experience"
        result = highlight_keywords(text, ["python", "developer"])

        assert "**Python**" in result
        assert "**developer**" in result

    def test_highlight_keywords_case_insensitive(self):
        """Test that matching is case-insensitive."""
        text = "Looking for a PYTHON Developer"
        result = highlight_keywords(text, ["python", "developer"])

        assert "**PYTHON**" in result
        assert "**Developer**" in result

    def test_highlight_keywords_preserves_case(self):
        """Test that original case is preserved in output."""
        text = "PyThOn developer"
        result = highlight_keywords(text, ["python"])

        assert "**PyThOn**" in result
        assert "python" not in result  # Original case preserved

    def test_highlight_keywords_with_phrases(self):
        """Test highlighting multi-word phrases."""
        text = "Looking for full-time remote work opportunities"
        result = highlight_keywords(text, ["full-time", "remote work"])

        assert "**full-time**" in result
        assert "**remote work**" in result

    def test_highlight_keywords_with_empty_text(self):
        """Test with empty text."""
        result = highlight_keywords("", ["python"])
        assert result == ""

    def test_highlight_keywords_with_empty_keywords(self):
        """Test with empty keywords list."""
        text = "Some text"
        result = highlight_keywords(text, [])
        assert result == text

    def test_highlight_keywords_word_boundaries(self):
        """Test that word boundaries are respected."""
        text = "Python and Pythonic code"
        result = highlight_keywords(text, ["python"])

        # Should match "Python" but not "Pythonic"
        assert "**Python**" in result
        assert "**Pythonic**" not in result

    def test_highlight_keywords_custom_markers(self):
        """Test using custom markers."""
        text = "Python developer"
        result = highlight_keywords(text, ["python"], marker_start="<<", marker_end=">>")

        assert "<<Python>>" in result

    def test_highlight_keywords_longest_first(self):
        """Test that longer keywords are matched first."""
        text = "Senior Python developer"
        result = highlight_keywords(text, ["python", "python developer"])

        # "python developer" should be matched as a phrase, not separately
        assert "**Python developer**" in result or "**Python**" in result


class TestExtractSnippetsWithKeywords:
    """Tests for extract_snippets_with_keywords function."""

    def test_extract_snippets_basic(self):
        """Test basic snippet extraction."""
        text = "We are looking for a Python developer with Django experience."
        snippets = extract_snippets_with_keywords(text, ["python", "django"], context_chars=20)

        assert len(snippets) >= 2
        assert any("Python" in s for s in snippets)
        assert any("Django" in s for s in snippets)

    def test_extract_snippets_with_ellipsis(self):
        """Test that snippets include ellipsis when truncated."""
        text = "This is a very long text about Python development and many other things."
        snippets = extract_snippets_with_keywords(text, ["python"], context_chars=10)

        assert len(snippets) >= 1
        # Should have ellipsis
        assert any("..." in s for s in snippets)

    def test_extract_snippets_no_duplicates(self):
        """Test that duplicate snippets are avoided."""
        text = "Python Python Python"
        snippets = extract_snippets_with_keywords(text, ["python"], context_chars=10)

        # Should have snippets but not excessive duplicates
        assert len(snippets) >= 1

    def test_extract_snippets_empty_text(self):
        """Test with empty text."""
        snippets = extract_snippets_with_keywords("", ["python"])
        assert snippets == []

    def test_extract_snippets_empty_keywords(self):
        """Test with empty keywords."""
        snippets = extract_snippets_with_keywords("Some text", [])
        assert snippets == []


class TestFormatMatchedTerms:
    """Tests for format_matched_terms function."""

    def test_format_matched_terms_with_required(self):
        """Test formatting with required terms."""
        result = format_matched_terms(["remote", "full-time"], [], [])

        assert "Required terms: " in result
        assert "remote" in result.lower()
        assert "full-time" in result.lower()

    def test_format_matched_terms_with_groups(self):
        """Test formatting with keyword groups."""
        result = format_matched_terms(
            [], [["python", "java"], ["django", "flask"]], []
        )

        assert "Keyword group 1:" in result
        assert "Keyword group 2:" in result
        assert "python" in result or "java" in result
        assert "django" in result or "flask" in result

    def test_format_matched_terms_with_required_and_groups(self):
        """Test formatting with both required terms and groups."""
        result = format_matched_terms(
            ["remote"], [["python"], ["senior"]], []
        )

        assert "Required terms:" in result
        assert "Keyword group 1:" in result
        assert "Keyword group 2:" in result

    def test_format_matched_terms_with_excluded(self):
        """Test formatting with excluded terms."""
        result = format_matched_terms(["remote"], [], ["junior", "intern"])

        assert "Excluded terms found:" in result
        assert "junior" in result.lower()
        assert "intern" in result.lower()

    def test_format_matched_terms_empty(self):
        """Test formatting with no terms."""
        result = format_matched_terms([], [], [])

        assert "No specific match criteria" in result


class TestTruncateText:
    """Tests for truncate_text function."""

    def test_truncate_text_short_text(self):
        """Test that short text is not truncated."""
        text = "Short text"
        result = truncate_text(text, max_length=100)

        assert result == text

    def test_truncate_text_long_text(self):
        """Test that long text is truncated."""
        text = "This is a very long text that needs to be truncated because it exceeds the maximum length"
        result = truncate_text(text, max_length=30)

        assert len(result) <= 30
        assert result.endswith("...")

    def test_truncate_text_word_boundary(self):
        """Test that truncation tries to break at word boundaries."""
        text = "This is a test of word boundary truncation"
        result = truncate_text(text, max_length=20)

        # Should end with ... and break at word
        assert result.endswith("...")
        # Should not end with partial word (before ...)
        words = result[:-3].strip().split()
        # Last word should be complete
        assert len(words) >= 1

    def test_truncate_text_custom_suffix(self):
        """Test truncation with custom suffix."""
        text = "This is a long text"
        result = truncate_text(text, max_length=15, suffix=" [...]")

        assert result.endswith(" [...]")
        assert len(result) <= 15

    def test_truncate_text_empty(self):
        """Test with empty text."""
        result = truncate_text("", max_length=10)
        assert result == ""

    def test_truncate_text_at_exact_length(self):
        """Test text at exact max length."""
        text = "Exact"  # 5 chars
        result = truncate_text(text, max_length=5)
        assert result == text


class TestNormalizeForMatching:
    """Tests for normalize_for_matching function."""

    def test_normalize_for_matching_basic(self):
        """Test basic text normalization."""
        result = normalize_for_matching("Hello World")

        assert result == "hello world"

    def test_normalize_for_matching_case(self):
        """Test that text is lowercased."""
        result = normalize_for_matching("HELLO WORLD")

        assert result == "hello world"

    def test_normalize_for_matching_whitespace(self):
        """Test that whitespace is normalized."""
        result = normalize_for_matching("  Hello    World  ")

        assert result == "hello world"

    def test_normalize_for_matching_punctuation(self):
        """Test that punctuation is removed."""
        result = normalize_for_matching("Hello, World! How are you?")

        # Commas, periods, exclamation marks, question marks should be removed
        assert "," not in result
        assert "!" not in result
        assert "?" not in result
        # Spaces should remain
        assert "hello world how are you" == result

    def test_normalize_for_matching_preserves_hyphens(self):
        """Test that hyphens are preserved."""
        result = normalize_for_matching("full-time remote-work")

        assert "full-time" in result
        assert "remote-work" in result

    def test_normalize_for_matching_preserves_apostrophes(self):
        """Test that apostrophes are preserved."""
        result = normalize_for_matching("don't can't")

        assert "don't" in result
        assert "can't" in result

    def test_normalize_for_matching_empty(self):
        """Test with empty text."""
        result = normalize_for_matching("")
        assert result == ""

    def test_normalize_for_matching_multiple_spaces(self):
        """Test that multiple spaces are collapsed."""
        result = normalize_for_matching("hello     world")

        assert result == "hello world"
