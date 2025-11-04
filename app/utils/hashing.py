"""Hashing utilities for generating job keys and content hashes.

This module provides deterministic hashing functions for:
- job_key: unique identifier from source info + external_id
- content_hash: change detection from title + description + location
"""

import hashlib
from typing import Optional


def compute_job_key(source_type: str, source_identifier: str, external_id: str) -> str:
    """Compute a unique job key from source information and external ID.

    The job key is a SHA256 hash of: source_type:source_identifier:external_id
    This ensures jobs are unique across all sources and prevents duplicates.

    Args:
        source_type: ATS type (greenhouse, lever, ashby)
        source_identifier: Company identifier in the ATS
        external_id: Job ID from the ATS

    Returns:
        Hexadecimal string representation of SHA256 hash (64 characters)

    Example:
        >>> compute_job_key("greenhouse", "examplecorp", "12345")
        'a3f2e1d9c8b7a6f5e4d3c2b1a0987654321fedcba0123456789abcdef0123456'
    """
    # Normalize inputs to lowercase for consistency
    source_type = source_type.lower().strip()
    source_identifier = source_identifier.lower().strip()
    external_id = external_id.strip()

    # Construct the composite key
    composite_key = f"{source_type}:{source_identifier}:{external_id}"

    # Compute SHA256 hash
    hash_obj = hashlib.sha256(composite_key.encode("utf-8"))
    return hash_obj.hexdigest()


def compute_content_hash(title: str, description: str, location: Optional[str] = None) -> str:
    """Compute a content hash for change detection.

    The content hash is a SHA256 hash of normalized title + description + location.
    This allows us to detect meaningful content changes even if the ATS updated_at
    timestamp doesn't change.

    Text is normalized by:
    - Converting to lowercase
    - Stripping leading/trailing whitespace
    - Replacing multiple whitespace with single space

    Args:
        title: Job title
        description: Full job description text
        location: Optional job location

    Returns:
        Hexadecimal string representation of SHA256 hash (64 characters)

    Example:
        >>> compute_content_hash("Software Engineer", "Great job...", "Remote")
        'b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3'
    """
    # Normalize text fields
    normalized_title = _normalize_text(title)
    normalized_description = _normalize_text(description)
    normalized_location = _normalize_text(location) if location else ""

    # Construct composite content string
    composite_content = f"{normalized_title}\n{normalized_description}\n{normalized_location}"

    # Compute SHA256 hash
    hash_obj = hashlib.sha256(composite_content.encode("utf-8"))
    return hash_obj.hexdigest()


def _normalize_text(text: str) -> str:
    """Normalize text for consistent hashing.

    Normalization steps:
    1. Convert to lowercase
    2. Strip leading/trailing whitespace
    3. Replace multiple whitespace characters with single space

    Args:
        text: Text to normalize

    Returns:
        Normalized text string
    """
    # Convert to lowercase
    normalized = text.lower()

    # Strip leading/trailing whitespace
    normalized = normalized.strip()

    # Replace multiple whitespace with single space
    import re

    normalized = re.sub(r"\s+", " ", normalized)

    return normalized


def hash_string(value: str) -> str:
    """Compute SHA256 hash of a string value.

    General-purpose hashing function for any string value.

    Args:
        value: String to hash

    Returns:
        Hexadecimal string representation of SHA256 hash (64 characters)
    """
    hash_obj = hashlib.sha256(value.encode("utf-8"))
    return hash_obj.hexdigest()
