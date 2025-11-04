"""Custom exceptions for configuration management."""

from typing import List, Optional


class ConfigurationError(Exception):
    """
    Exception raised when configuration validation fails.

    This exception can store multiple validation errors and format them
    in a human-readable way with helpful suggestions.
    """

    def __init__(
        self,
        message: str,
        errors: Optional[List[str]] = None,
        suggestions: Optional[List[str]] = None,
    ):
        """
        Initialize ConfigurationError.

        Args:
            message: Primary error message
            errors: List of specific validation errors
            suggestions: List of helpful suggestions to fix the errors
        """
        self.message = message
        self.errors = errors or []
        self.suggestions = suggestions or []
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        """Format the error message with all errors and suggestions."""
        parts = [self.message]

        if self.errors:
            parts.append("\nValidation Errors:")
            for i, error in enumerate(self.errors, 1):
                parts.append(f"  {i}. {error}")

        if self.suggestions:
            parts.append("\nSuggestions:")
            for suggestion in self.suggestions:
                parts.append(f"  - {suggestion}")

        return "\n".join(parts)

    def add_error(self, error: str) -> None:
        """Add a validation error to the list."""
        self.errors.append(error)

    def add_suggestion(self, suggestion: str) -> None:
        """Add a helpful suggestion to the list."""
        self.suggestions.append(suggestion)
