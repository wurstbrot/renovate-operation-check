"""
Custom exceptions for Renovate Operation Check
"""


class RenovateOperationError(Exception):
    """Base exception for all Renovate Operation Check errors"""

    pass


class ConfigurationError(RenovateOperationError):
    """Raised when configuration is invalid or missing"""

    pass


class BitbucketAuthError(RenovateOperationError):
    """Raised when Bitbucket authentication fails"""

    pass


class ResponseValidationError(RenovateOperationError):
    """Raised when an API response does not match the expected schema"""

    pass


class BitbucketApiError(RenovateOperationError):
    """Raised when a Bitbucket API call could not be completed.

    Distinguishes "the server answered, and the result is empty" from "the
    result is unknown because the call failed". Returning an empty list for
    the second case let authentication and transport failures pass as a
    successful cleanup run.
    """

    pass
