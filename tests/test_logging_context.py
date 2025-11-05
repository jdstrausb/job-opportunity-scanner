"""Tests for logging context propagation."""

import pytest

from app.logging.context import (
    clear_log_context,
    get_log_context,
    log_context,
    pop_log_context,
    push_log_context,
)


@pytest.fixture(autouse=True)
def clean_context():
    """Clear logging context before and after each test."""
    clear_log_context()
    yield
    clear_log_context()


def test_empty_context():
    """Test that context starts empty."""
    assert get_log_context() == {}


def test_push_single_field():
    """Test pushing a single field to context."""
    token = push_log_context(run_id="abc123")
    assert get_log_context() == {"run_id": "abc123"}
    pop_log_context(token)
    assert get_log_context() == {}


def test_push_multiple_fields():
    """Test pushing multiple fields at once."""
    token = push_log_context(run_id="abc123", source_id="acme-corp", job_key="job-1")
    context = get_log_context()
    assert context == {
        "run_id": "abc123",
        "source_id": "acme-corp",
        "job_key": "job-1",
    }
    pop_log_context(token)
    assert get_log_context() == {}


def test_nested_context():
    """Test nested context pushes and pops."""
    # Push first layer
    token1 = push_log_context(run_id="abc123")
    assert get_log_context() == {"run_id": "abc123"}

    # Push second layer
    token2 = push_log_context(source_id="acme-corp")
    assert get_log_context() == {"run_id": "abc123", "source_id": "acme-corp"}

    # Push third layer
    token3 = push_log_context(job_key="job-1")
    assert get_log_context() == {
        "run_id": "abc123",
        "source_id": "acme-corp",
        "job_key": "job-1",
    }

    # Pop layers in reverse order
    pop_log_context(token3)
    assert get_log_context() == {"run_id": "abc123", "source_id": "acme-corp"}

    pop_log_context(token2)
    assert get_log_context() == {"run_id": "abc123"}

    pop_log_context(token1)
    assert get_log_context() == {}


def test_context_override():
    """Test that pushing the same key overwrites previous value."""
    token1 = push_log_context(run_id="abc123")
    assert get_log_context() == {"run_id": "abc123"}

    token2 = push_log_context(run_id="xyz789")
    assert get_log_context() == {"run_id": "xyz789"}

    pop_log_context(token2)
    assert get_log_context() == {"run_id": "abc123"}

    pop_log_context(token1)
    assert get_log_context() == {}


def test_context_manager_basic():
    """Test basic context manager usage."""
    assert get_log_context() == {}

    with log_context(run_id="abc123"):
        assert get_log_context() == {"run_id": "abc123"}

    assert get_log_context() == {}


def test_context_manager_nested():
    """Test nested context managers."""
    with log_context(run_id="abc123"):
        assert get_log_context() == {"run_id": "abc123"}

        with log_context(source_id="acme-corp"):
            assert get_log_context() == {"run_id": "abc123", "source_id": "acme-corp"}

            with log_context(job_key="job-1"):
                assert get_log_context() == {
                    "run_id": "abc123",
                    "source_id": "acme-corp",
                    "job_key": "job-1",
                }

            assert get_log_context() == {"run_id": "abc123", "source_id": "acme-corp"}

        assert get_log_context() == {"run_id": "abc123"}

    assert get_log_context() == {}


def test_context_manager_exception():
    """Test that context is restored even when exception occurs."""
    assert get_log_context() == {}

    try:
        with log_context(run_id="abc123"):
            assert get_log_context() == {"run_id": "abc123"}
            raise ValueError("Test exception")
    except ValueError:
        pass

    # Context should be restored even after exception
    assert get_log_context() == {}


def test_context_manager_multiple_fields():
    """Test context manager with multiple fields."""
    with log_context(run_id="abc123", source_id="acme-corp", job_key="job-1"):
        assert get_log_context() == {
            "run_id": "abc123",
            "source_id": "acme-corp",
            "job_key": "job-1",
        }

    assert get_log_context() == {}


def test_clear_context():
    """Test clearing all context."""
    push_log_context(run_id="abc123", source_id="acme-corp")
    assert get_log_context() != {}

    clear_log_context()
    assert get_log_context() == {}


def test_context_isolation():
    """Test that get_log_context returns a copy, not the actual dict."""
    token = push_log_context(run_id="abc123")

    # Get context and try to modify it
    context = get_log_context()
    context["source_id"] = "modified"

    # Original context should be unchanged
    assert get_log_context() == {"run_id": "abc123"}

    pop_log_context(token)
