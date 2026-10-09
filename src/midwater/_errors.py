"""Exceptions raised by the Midwater SDK.

No exception message or repr ever contains the API key.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from .types import Conversation

__all__ = [
    "MidwaterError",
    "APIError",
    "AuthenticationError",
    "PermissionDeniedError",
    "ValidationError",
    "NotFoundError",
    "RequestTimeoutError",
    "IdempotencyConflictError",
    "PayloadTooLargeError",
    "RateLimitError",
    "ServerError",
    "ServiceUnavailableError",
    "APIConnectionError",
    "WaitTimeoutError",
    "WebhookVerificationError",
]


class MidwaterError(Exception):
    """Base class for every error raised by this SDK."""


class APIError(MidwaterError):
    """The Midwater API answered with an error (or the SDK could not use the answer).

    Attributes:
        status: The HTTP status code, or ``None`` when no request was made
            (for example a missing API key at construction).
        type: The machine-readable error type from the response (``authentication_error``,
            ``validation_error``, ``not_found`` ...), if any. New types may appear; the
            exception class is chosen from the HTTP status, so an unknown type never breaks
            error handling.
        message: The human-readable message.
        fields: Validation messages keyed by dotted path (``transcript.0.speaker``);
            empty when the response has none.
        body: The parsed JSON error body, or the raw text when it wasn't JSON.
        request_id: The ID of the failed request, for support: ``error.request_id`` from the
            body, else the ``Midwater-Request-Id`` response header, else ``None``. Both are
            planned on the server side, so this is ``None`` for now.
    """

    status: Optional[int]
    type: Optional[str]
    message: str
    fields: Dict[str, List[str]]
    body: Any
    request_id: Optional[str]

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        type: Optional[str] = None,
        fields: Optional[Dict[str, List[str]]] = None,
        body: Any = None,
        request_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.type = type
        self.fields = dict(fields) if fields else {}
        self.body = body
        self.request_id = request_id

    def __str__(self) -> str:
        parts = []
        if self.status is not None:
            parts.append(f"status {self.status}")
        if self.type:
            parts.append(self.type)
        if self.request_id:
            parts.append(f"request {self.request_id}")
        suffix = f" ({', '.join(parts)})" if parts else ""
        return f"{self.message}{suffix}"

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(message={self.message!r}, status={self.status!r}, "
            f"type={self.type!r}, request_id={self.request_id!r})"
        )


class AuthenticationError(APIError):
    """401, or no usable API key was configured."""


class PermissionDeniedError(APIError):
    """403: the API key can't do this."""


class ValidationError(APIError):
    """400 (invalid JSON) or 422 (the body doesn't match the schema). See ``.fields``."""


class NotFoundError(APIError):
    """404: not found in the API key's environment."""


class MethodNotAllowedError(APIError):
    """405: the path doesn't support this method.

    Planned as a JSON error with an ``Allow`` header listing the supported methods.
    """


class RequestTimeoutError(APIError):
    """408: the server timed out waiting for the request."""


class IdempotencyConflictError(APIError):
    """409: this ``Idempotency-Key`` was already used with a different body."""


class PayloadTooLargeError(APIError):
    """413: the request body is too large."""


class RateLimitError(APIError):
    """429: too many requests."""


class ServerError(APIError):
    """5xx: something went wrong on Midwater's side."""


class ServiceUnavailableError(ServerError):
    """503: Midwater is briefly unavailable."""


class APIConnectionError(MidwaterError):
    """The request never got an HTTP answer (DNS, refused connection, timeout ...)."""


class WaitTimeoutError(MidwaterError, TimeoutError):
    """``conversations.wait()`` gave up before scoring finished.

    ``conversation`` holds the last state that was read (``None`` if nothing was read).
    """

    def __init__(self, message: str, conversation: Optional[Conversation] = None) -> None:
        super().__init__(message)
        self.conversation = conversation


class WebhookVerificationError(MidwaterError):
    """A webhook delivery failed verification.

    ``reason`` is one of ``missing_header``, ``malformed_header``, ``stale_timestamp``,
    ``invalid_signature`` or ``no_secret``.
    """

    def __init__(self, reason: str, message: Optional[str] = None) -> None:
        super().__init__(message or reason)
        self.reason = reason
