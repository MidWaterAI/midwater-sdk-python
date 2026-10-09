"""Python SDK for Midwater: send AI-agent conversations, read check results and agent health,
and verify webhooks."""

from __future__ import annotations

from . import types, webhooks
from ._client import AsyncMidwater, Midwater
from ._errors import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    IdempotencyConflictError,
    MethodNotAllowedError,
    MidwaterError,
    NotFoundError,
    PayloadTooLargeError,
    PermissionDeniedError,
    RateLimitError,
    RequestTimeoutError,
    ServerError,
    ServiceUnavailableError,
    ValidationError,
    WaitTimeoutError,
    WebhookVerificationError,
)
from ._version import __version__
from .types import (
    AgentHealth,
    CheckResult,
    Conversation,
    ConversationAccepted,
    ConversationCreate,
    Feedback,
    FeedbackCreate,
    GroupHealth,
    HealthWindow,
    WebhookEvent,
)
from .webhooks import verify as verify_webhook

__all__ = [
    "__version__",
    "Midwater",
    "AsyncMidwater",
    "MidwaterError",
    "APIError",
    "AuthenticationError",
    "PermissionDeniedError",
    "ValidationError",
    "NotFoundError",
    "RequestTimeoutError",
    "IdempotencyConflictError",
    "MethodNotAllowedError",
    "PayloadTooLargeError",
    "RateLimitError",
    "ServerError",
    "ServiceUnavailableError",
    "APIConnectionError",
    "WaitTimeoutError",
    "WebhookVerificationError",
    "webhooks",
    "verify_webhook",
    "types",
    "AgentHealth",
    "CheckResult",
    "Conversation",
    "ConversationAccepted",
    "ConversationCreate",
    "Feedback",
    "FeedbackCreate",
    "GroupHealth",
    "HealthWindow",
    "WebhookEvent",
]
