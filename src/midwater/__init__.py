"""Python SDK for Midwater: send AI-agent conversations, read check results and agent health,
and verify webhooks."""

from __future__ import annotations

from . import types, webhooks
from ._base import DEFAULT_BASE_URL
from ._client import AsyncMidwater, Midwater
from ._errors import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    MidwaterError,
    NotFoundError,
    RateLimitError,
    ServerError,
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

__all__ = [
    "__version__",
    "DEFAULT_BASE_URL",
    "Midwater",
    "AsyncMidwater",
    "MidwaterError",
    "APIError",
    "AuthenticationError",
    "ValidationError",
    "NotFoundError",
    "RateLimitError",
    "ServerError",
    "APIConnectionError",
    "WaitTimeoutError",
    "WebhookVerificationError",
    "webhooks",
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
