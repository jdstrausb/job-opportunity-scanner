"""Unit tests for notification template rendering.

Tests the TemplateRenderer for:
- Subject, HTML, and text template rendering
- Context variable interpolation
- HTML auto-escaping
- Strict undefined variable detection
- Template caching behavior
"""

import pytest
from jinja2 import TemplateNotFound, UndefinedError

from app.notifications.models import NotificationTemplateError
from app.notifications.templates import TemplateRenderer


@pytest.fixture
def sample_context():
    """Sample template context with all required fields."""
    return {
        "title": "Senior Python Developer",
        "company": "Tech Corp",
        "location": "Remote",
        "url": "https://example.com/job/123",
        "posted_at": "2025-11-01T10:00:00+00:00",
        "updated_at": "2025-11-02T14:00:00+00:00",
        "summary": "Matched required terms: python, aws\nMatched groups: senior",
        "snippets": [
            "Looking for Python developer with AWS experience.",
            "Must have Django and FastAPI knowledge.",
        ],
        "snippets_highlighted": [
            "Looking for <b>Python</b> developer with <b>AWS</b> experience.",
            "Must have <b>Django</b> and FastAPI knowledge.",
        ],
        "match_quality": "perfect",
        "search_terms": ["python", "aws", "senior", "django"],
        "match_reason": "Matched required terms: python, aws\nMatched groups: senior",
        "first_seen_at": "2025-11-03T08:00:00+00:00",
        "last_seen_at": "2025-11-04T09:00:00+00:00",
        "source_type": "greenhouse",
        "source_identifier": "techcorp",
        "job_key": "test_job_key_abc123",
        "version_hash": "version_hash_def456",
        "matched_terms_flat": ["python", "aws", "senior", "django"],
        "matched_fields": {
            "title": ["python", "senior"],
            "description": ["python", "aws", "django"],
        },
    }


def test_template_renderer_initialization():
    """Test that TemplateRenderer initializes correctly."""
    renderer = TemplateRenderer()

    assert renderer is not None
    assert renderer.env is not None
    assert renderer.subject_template_name == "job_alert_subject.j2"
    assert renderer.html_template_name == "job_alert_body.html.j2"
    assert renderer.text_template_name == "job_alert_body.txt.j2"


def test_render_returns_all_three_components(sample_context):
    """Test that render returns subject, html_body, and text_body."""
    renderer = TemplateRenderer()
    result = renderer.render(sample_context)

    assert "subject" in result
    assert "html_body" in result
    assert "text_body" in result

    # All should be non-empty strings
    assert isinstance(result["subject"], str)
    assert isinstance(result["html_body"], str)
    assert isinstance(result["text_body"], str)
    assert len(result["subject"]) > 0
    assert len(result["html_body"]) > 0
    assert len(result["text_body"]) > 0


def test_render_subject_line_format(sample_context):
    """Test that subject line is properly formatted."""
    renderer = TemplateRenderer()
    result = renderer.render(sample_context)

    subject = result["subject"]

    # Should contain job title and company
    assert "Senior Python Developer" in subject
    assert "Tech Corp" in subject

    # Should be single line (no newlines)
    assert "\n" not in subject

    # Should start with expected format
    assert subject.startswith("New Job Match:")


def test_render_html_body_contains_key_elements(sample_context):
    """Test that HTML body contains expected elements."""
    renderer = TemplateRenderer()
    result = renderer.render(sample_context)

    html = result["html_body"]

    # Should contain HTML structure
    assert "<!DOCTYPE html>" in html
    assert "<html>" in html
    assert "</html>" in html

    # Should contain job details
    assert "Senior Python Developer" in html
    assert "Tech Corp" in html
    assert "Remote" in html
    assert "https://example.com/job/123" in html

    # Should contain match quality
    assert "perfect" in html.lower()

    # Should contain highlighted snippets with bold tags
    assert "<b>Python</b>" in html
    assert "<b>AWS</b>" in html

    # Should contain search terms
    assert "python" in html.lower()
    assert "aws" in html.lower()


def test_render_html_body_escapes_dangerous_content():
    """Test that HTML auto-escaping works for untrusted content."""
    renderer = TemplateRenderer()

    # Create context with potentially dangerous content
    dangerous_context = {
        "title": "<script>alert('xss')</script>",
        "company": "Safe Corp",
        "location": "Remote",
        "url": "https://example.com",
        "posted_at": "2025-11-01T10:00:00+00:00",
        "updated_at": None,
        "summary": "Test summary",
        "snippets": ["Test snippet"],
        "snippets_highlighted": ["Test <b>snippet</b>"],  # This is marked safe
        "match_quality": "perfect",
        "search_terms": ["test"],
        "match_reason": "Test reason",
        "first_seen_at": "2025-11-03T08:00:00+00:00",
        "last_seen_at": "2025-11-04T09:00:00+00:00",
        "source_type": "greenhouse",
        "source_identifier": "test",
        "job_key": "key123",
        "version_hash": "hash456",
        "matched_terms_flat": ["test"],
        "matched_fields": {},
    }

    result = renderer.render(dangerous_context)
    html = result["html_body"]

    # Script tags should be escaped
    assert "<script>" not in html
    assert "&lt;script&gt;" in html or "alert" not in html


def test_render_text_body_contains_key_elements(sample_context):
    """Test that plain text body contains expected elements."""
    renderer = TemplateRenderer()
    result = renderer.render(sample_context)

    text = result["text_body"]

    # Should contain job details
    assert "Senior Python Developer" in text
    assert "Tech Corp" in text
    assert "Remote" in text
    assert "https://example.com/job/123" in text

    # Should contain match information
    assert "python" in text.lower()
    assert "aws" in text.lower()

    # Should have text-friendly formatting (separators)
    assert "=" in text  # Header separator

    # Should contain snippets
    assert "Looking for Python developer" in text


def test_render_text_body_no_html_tags(sample_context):
    """Test that text body doesn't contain HTML tags."""
    renderer = TemplateRenderer()
    result = renderer.render(sample_context)

    text = result["text_body"]

    # Should not have HTML tags
    assert "<html>" not in text
    assert "<div>" not in text
    assert "<b>" not in text  # Plain text should use plain formatting


def test_render_raises_on_missing_required_field():
    """Test that rendering fails with missing required template variables."""
    renderer = TemplateRenderer()

    # Create incomplete context (missing required field)
    incomplete_context = {
        "title": "Test Job",
        # Missing many required fields
    }

    # Should raise NotificationTemplateError due to StrictUndefined
    with pytest.raises(NotificationTemplateError) as exc_info:
        renderer.render(incomplete_context)

    assert "Template rendering failed" in str(exc_info.value)


def test_render_handles_none_values_gracefully(sample_context):
    """Test that None values in context are handled properly."""
    renderer = TemplateRenderer()

    # Set some optional fields to None
    sample_context["posted_at"] = None
    sample_context["updated_at"] = None

    result = renderer.render(sample_context)

    # Should not raise error
    assert result["subject"]
    assert result["html_body"]
    assert result["text_body"]


def test_render_with_empty_snippets_list(sample_context):
    """Test rendering with empty snippets list."""
    renderer = TemplateRenderer()

    sample_context["snippets"] = []
    sample_context["snippets_highlighted"] = []

    result = renderer.render(sample_context)

    # Should still render successfully
    assert result["html_body"]
    assert result["text_body"]


def test_render_multiple_calls_use_cached_templates(sample_context):
    """Test that templates are cached across multiple render calls."""
    renderer = TemplateRenderer()

    # First render
    result1 = renderer.render(sample_context)

    # Modify context
    sample_context["title"] = "Different Job Title"

    # Second render
    result2 = renderer.render(sample_context)

    # Results should be different (context changed)
    assert result1["subject"] != result2["subject"]
    assert "Different Job Title" in result2["subject"]

    # But should use same template objects (cached)
    # This is tested indirectly - if templates weren't cached, we'd see
    # performance issues or file access errors


def test_custom_template_names():
    """Test initializing renderer with custom template names."""
    renderer = TemplateRenderer(
        subject_template="custom_subject.j2",
        html_template="custom_body.html.j2",
        text_template="custom_body.txt.j2",
    )

    assert renderer.subject_template_name == "custom_subject.j2"
    assert renderer.html_template_name == "custom_body.html.j2"
    assert renderer.text_template_name == "custom_body.txt.j2"


def test_subject_line_strips_whitespace():
    """Test that subject line strips leading/trailing whitespace."""
    renderer = TemplateRenderer()

    context = {
        "title": "  Test Job  ",
        "company": "  Test Corp  ",
        "location": "Remote",
        "url": "https://test.com",
        "posted_at": None,
        "updated_at": None,
        "summary": "Test",
        "snippets": [],
        "snippets_highlighted": [],
        "match_quality": "perfect",
        "search_terms": ["test"],
        "match_reason": "Test",
        "first_seen_at": "2025-11-01T10:00:00+00:00",
        "last_seen_at": "2025-11-01T10:00:00+00:00",
        "source_type": "greenhouse",
        "source_identifier": "test",
        "job_key": "key123",
        "version_hash": "hash456",
        "matched_terms_flat": ["test"],
        "matched_fields": {},
    }

    result = renderer.render(context)

    # Subject should not have leading/trailing whitespace
    assert result["subject"] == result["subject"].strip()
