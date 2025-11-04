"""Custom exceptions for ATS adapters."""


class AdapterError(Exception):
    """Base exception for all adapter errors.

    This is the parent class for all adapter-specific exceptions. Catching this
    exception will catch any adapter-related error that should be handled at the
    pipeline level (e.g., skip to next source without aborting).
    """

    pass


class AdapterHTTPError(AdapterError):
    """HTTP request failed with 4xx or 5xx error.

    Indicates an HTTP request to an ATS API endpoint returned an error status code.
    These errors may be transient (5xx) or permanent (4xx) depending on the status.
    """

    def __init__(self, message: str, status_code: int, url: str) -> None:
        """Initialize HTTP error with status code and URL.

        Args:
            message: Human-readable error message
            status_code: HTTP status code (e.g., 404, 500)
            url: URL that failed
        """
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class AdapterTimeoutError(AdapterError):
    """HTTP request timed out.

    Indicates a request to an ATS API endpoint did not complete within the
    configured timeout period. This is typically a transient error.
    """

    def __init__(self, message: str, url: str) -> None:
        """Initialize timeout error with URL.

        Args:
            message: Human-readable error message
            url: URL that timed out
        """
        super().__init__(message)
        self.url = url


class AdapterResponseError(AdapterError):
    """Response parsing or validation failed.

    Indicates the adapter received a response but could not parse or validate it
    (e.g., invalid JSON, missing required fields, GraphQL errors).
    """

    pass


class AdapterConfigurationError(AdapterError):
    """Invalid adapter configuration.

    Indicates the adapter was given invalid configuration (e.g., unsupported
    ATS type, missing required credentials).
    """

    pass
