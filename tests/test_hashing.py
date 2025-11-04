"""Unit tests for hashing utilities."""

import pytest

from app.utils.hashing import compute_content_hash, compute_job_key, hash_string


class TestComputeJobKey:
    """Tests for compute_job_key function."""

    def test_compute_job_key_basic(self):
        """Test basic job key computation."""
        job_key = compute_job_key("greenhouse", "examplecorp", "12345")

        # Should return a 64-character hex string (SHA256)
        assert len(job_key) == 64
        assert all(c in "0123456789abcdef" for c in job_key)

    def test_compute_job_key_deterministic(self):
        """Test that job key computation is deterministic."""
        key1 = compute_job_key("greenhouse", "examplecorp", "12345")
        key2 = compute_job_key("greenhouse", "examplecorp", "12345")

        assert key1 == key2

    def test_compute_job_key_different_for_different_inputs(self):
        """Test that different inputs produce different keys."""
        key1 = compute_job_key("greenhouse", "examplecorp", "12345")
        key2 = compute_job_key("greenhouse", "examplecorp", "12346")
        key3 = compute_job_key("lever", "examplecorp", "12345")
        key4 = compute_job_key("greenhouse", "differentcorp", "12345")

        # All keys should be different
        assert key1 != key2
        assert key1 != key3
        assert key1 != key4
        assert key2 != key3
        assert key2 != key4
        assert key3 != key4

    def test_compute_job_key_normalizes_to_lowercase(self):
        """Test that source_type and source_identifier are normalized to lowercase."""
        key1 = compute_job_key("GREENHOUSE", "ExampleCorp", "12345")
        key2 = compute_job_key("greenhouse", "examplecorp", "12345")

        assert key1 == key2

    def test_compute_job_key_strips_whitespace(self):
        """Test that whitespace is stripped from inputs."""
        key1 = compute_job_key("  greenhouse  ", "  examplecorp  ", "  12345  ")
        key2 = compute_job_key("greenhouse", "examplecorp", "12345")

        assert key1 == key2

    def test_compute_job_key_external_id_case_sensitive(self):
        """Test that external_id is case-sensitive."""
        key1 = compute_job_key("greenhouse", "examplecorp", "ABC123")
        key2 = compute_job_key("greenhouse", "examplecorp", "abc123")

        # External IDs are case-sensitive (not normalized to lowercase)
        assert key1 != key2


class TestComputeContentHash:
    """Tests for compute_content_hash function."""

    def test_compute_content_hash_basic(self):
        """Test basic content hash computation."""
        content_hash = compute_content_hash(
            "Software Engineer", "Great opportunity for a developer", "Remote"
        )

        # Should return a 64-character hex string (SHA256)
        assert len(content_hash) == 64
        assert all(c in "0123456789abcdef" for c in content_hash)

    def test_compute_content_hash_deterministic(self):
        """Test that content hash computation is deterministic."""
        hash1 = compute_content_hash(
            "Software Engineer", "Great opportunity for a developer", "Remote"
        )
        hash2 = compute_content_hash(
            "Software Engineer", "Great opportunity for a developer", "Remote"
        )

        assert hash1 == hash2

    def test_compute_content_hash_different_for_different_inputs(self):
        """Test that different inputs produce different hashes."""
        hash1 = compute_content_hash("Software Engineer", "Description 1", "Remote")
        hash2 = compute_content_hash("Senior Engineer", "Description 1", "Remote")
        hash3 = compute_content_hash("Software Engineer", "Description 2", "Remote")
        hash4 = compute_content_hash("Software Engineer", "Description 1", "New York")

        # All hashes should be different
        assert hash1 != hash2
        assert hash1 != hash3
        assert hash1 != hash4
        assert hash2 != hash3
        assert hash2 != hash4
        assert hash3 != hash4

    def test_compute_content_hash_normalizes_case(self):
        """Test that content is normalized to lowercase."""
        hash1 = compute_content_hash("SOFTWARE ENGINEER", "GREAT OPPORTUNITY", "REMOTE")
        hash2 = compute_content_hash("software engineer", "great opportunity", "remote")

        assert hash1 == hash2

    def test_compute_content_hash_normalizes_whitespace(self):
        """Test that whitespace is normalized."""
        hash1 = compute_content_hash(
            "  Software   Engineer  ", "  Great    opportunity  ", "  Remote  "
        )
        hash2 = compute_content_hash("Software Engineer", "Great opportunity", "Remote")

        assert hash1 == hash2

    def test_compute_content_hash_without_location(self):
        """Test computing content hash without location."""
        hash1 = compute_content_hash("Software Engineer", "Great opportunity", None)
        hash2 = compute_content_hash("Software Engineer", "Great opportunity", "")

        # Both should handle missing location
        assert len(hash1) == 64
        assert len(hash2) == 64

    def test_compute_content_hash_location_affects_hash(self):
        """Test that location affects the hash."""
        hash1 = compute_content_hash("Software Engineer", "Great opportunity", None)
        hash2 = compute_content_hash("Software Engineer", "Great opportunity", "Remote")

        assert hash1 != hash2


class TestHashString:
    """Tests for hash_string function."""

    def test_hash_string_basic(self):
        """Test basic string hashing."""
        hash_val = hash_string("test string")

        # Should return a 64-character hex string (SHA256)
        assert len(hash_val) == 64
        assert all(c in "0123456789abcdef" for c in hash_val)

    def test_hash_string_deterministic(self):
        """Test that string hashing is deterministic."""
        hash1 = hash_string("test string")
        hash2 = hash_string("test string")

        assert hash1 == hash2

    def test_hash_string_different_for_different_inputs(self):
        """Test that different inputs produce different hashes."""
        hash1 = hash_string("test string 1")
        hash2 = hash_string("test string 2")

        assert hash1 != hash2

    def test_hash_string_case_sensitive(self):
        """Test that string hashing is case-sensitive."""
        hash1 = hash_string("Test String")
        hash2 = hash_string("test string")

        assert hash1 != hash2

    def test_hash_string_empty(self):
        """Test hashing empty string."""
        hash_val = hash_string("")

        # Should still return a valid hash
        assert len(hash_val) == 64
        assert all(c in "0123456789abcdef" for c in hash_val)
