"""The synchronous ``Midwater`` and asynchronous ``AsyncMidwater`` clients."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Mapping
from types import TracebackType
from typing import Any, Dict, Optional, Type, Union

import httpx

from . import _base
from ._errors import APIConnectionError, WaitTimeoutError
from .types import (
    AgentHealth,
    Conversation,
    ConversationAccepted,
    ConversationCreate,
    Feedback,
    FeedbackCreate,
    FeedbackValue,
    GroupHealth,
)

__all__ = ["Midwater", "AsyncMidwater", "DEFAULT_BASE_URL"]

DEFAULT_BASE_URL = _base.DEFAULT_BASE_URL

ConversationPayload = Union[ConversationCreate, Mapping[str, Any]]


# Indirections so tests can replace the clock and sleeping without touching the stdlib.
def _sleep(seconds: float) -> None:
    time.sleep(seconds)


async def _async_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _monotonic() -> float:
    return time.monotonic()


def _feedback_body(check_key: str, verdict: FeedbackValue, note: Optional[str]) -> FeedbackCreate:
    body: FeedbackCreate = {"check_key": check_key, "verdict": verdict}
    if note is not None:
        body["note"] = note
    return body


def _wait_args(timeout: float, interval: float) -> None:
    if timeout < 0:
        raise ValueError("timeout must be >= 0")
    if interval <= 0:
        raise ValueError("interval must be > 0")


def _timeout_message(id: str, timeout: float, last: Optional[Conversation]) -> str:
    status = last.status if last is not None else "unknown"
    return f"Conversation {id!r} was not scored within {timeout:g}s (last status: {status})"


class _ClientConfig:
    def __init__(
        self,
        api_key: Optional[str],
        base_url: Optional[str],
        timeout: float,
        max_retries: int,
    ) -> None:
        self._api_key = _base.resolve_api_key(api_key)
        self.base_url = _base.resolve_base_url(base_url)
        self.timeout = timeout
        self.max_retries = _base.validate_max_retries(max_retries)

    def _prepare(
        self,
        method: str,
        path: str,
        body: Any,
        idempotency_key: Optional[str],
    ) -> Dict[str, Any]:
        has_body = body is not None
        return {
            "method": method,
            "url": f"{self.base_url}{path}",
            "headers": _base.build_headers(
                self._api_key, has_body=has_body, idempotency_key=idempotency_key
            ),
            "content": _base.encode_json(body) if has_body else None,
        }

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(base_url={self.base_url!r}, timeout={self.timeout!r}, "
            f"max_retries={self.max_retries!r})"
        )


# =========================================================================== sync


class Conversations:
    """``client.conversations``: send conversations and read their results."""

    def __init__(self, client: Midwater) -> None:
        self._client = client

    def create(
        self, conversation: ConversationPayload, idempotency_key: Optional[str] = None
    ) -> ConversationAccepted:
        """Send a finished conversation (``POST /v1/conversations``).

        When ``idempotency_key`` is omitted the SDK generates one (a UUID4) so automatic
        retries can never store the conversation twice. Pass your own (for example your
        ``external_id``) to make retries across processes safe too.
        """
        key = idempotency_key if idempotency_key is not None else str(uuid.uuid4())
        response = self._client._request(
            "POST", "/v1/conversations", body=conversation, idempotency_key=key, retryable=True
        )
        return ConversationAccepted.from_dict(
            _base.parse_json(response), replayed=_base.is_replayed(response), idempotency_key=key
        )

    def get(self, id: str) -> Conversation:
        """Get a conversation by Midwater's ID or your ``external_id``."""
        path = f"/v1/conversations/{_base.path_segment(id)}"
        response = self._client._request("GET", path, retryable=True)
        return Conversation.from_dict(_base.parse_json(response))

    def wait(self, id: str, timeout: float = 60.0, interval: float = 1.0) -> Conversation:
        """Poll ``get()`` until ``status`` is ``done`` or ``failed``.

        Raises ``WaitTimeoutError`` if that doesn't happen within ``timeout`` seconds.
        """
        _wait_args(timeout, interval)
        deadline = _monotonic() + timeout
        last: Optional[Conversation] = None
        while True:
            last = self.get(id)
            if last.status in _base.TERMINAL_STATUSES:
                return last
            remaining = deadline - _monotonic()
            if remaining <= 0:
                raise WaitTimeoutError(_timeout_message(id, timeout, last), last)
            _sleep(min(interval, remaining))

    def feedback(
        self,
        id: str,
        check_key: str,
        verdict: FeedbackValue,
        note: Optional[str] = None,
    ) -> Feedback:
        """Confirm (``pass``) or correct (``fail``) one check's result on one conversation.

        Each call adds a label, so the SDK does not retry this request on server errors.
        """
        path = f"/v1/conversations/{_base.path_segment(id)}/feedback"
        response = self._client._request(
            "POST", path, body=_feedback_body(check_key, verdict, note), retryable=False
        )
        return Feedback.from_dict(_base.parse_json(response))


class Agents:
    """``client.agents``."""

    def __init__(self, client: Midwater) -> None:
        self._client = client

    def health(self, agent_id: str) -> AgentHealth:
        """The agent's health in the API key's environment, with 7- and 30-day windows."""
        path = f"/v1/agents/{_base.path_segment(agent_id)}/health"
        return AgentHealth.from_dict(
            _base.parse_json(self._client._request("GET", path, retryable=True))
        )


class Groups:
    """``client.groups``."""

    def __init__(self, client: Midwater) -> None:
        self._client = client

    def health(self, group_id: str) -> GroupHealth:
        """The group's health: the worst status among its active agents, and totals."""
        path = f"/v1/groups/{_base.path_segment(group_id)}/health"
        return GroupHealth.from_dict(
            _base.parse_json(self._client._request("GET", path, retryable=True))
        )


class Midwater(_ClientConfig):
    """Synchronous Midwater API client.

    ``api_key`` defaults to ``MIDWATER_API_KEY``; ``base_url`` to ``MIDWATER_BASE_URL``, then
    ``DEFAULT_BASE_URL``. Pass ``http_client`` to use your own ``httpx.Client`` (proxies,
    custom transports); the SDK won't close a client you pass in.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = _base.DEFAULT_TIMEOUT,
        max_retries: int = _base.DEFAULT_MAX_RETRIES,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        super().__init__(api_key, base_url, timeout, max_retries)
        self._owns_http_client = http_client is None
        self._http = http_client if http_client is not None else httpx.Client(timeout=timeout)
        self.conversations = Conversations(self)
        self.agents = Agents(self)
        self.groups = Groups(self)

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        idempotency_key: Optional[str] = None,
        retryable: bool,
    ) -> httpx.Response:
        prepared = self._prepare(method, path, body, idempotency_key)
        attempt = 0
        while True:
            try:
                response = self._http.request(**prepared)
            except httpx.TransportError as exc:
                if retryable and attempt < self.max_retries:
                    _sleep(_base.retry_delay(attempt))
                    attempt += 1
                    continue
                raise APIConnectionError(f"Could not reach Midwater: {exc}") from exc
            except httpx.RequestError as exc:
                raise APIConnectionError(f"Could not reach Midwater: {exc}") from exc
            status = response.status_code
            if attempt < self.max_retries and _base.should_retry_status(
                status, retryable=retryable
            ):
                _sleep(_base.retry_delay(attempt, response.headers))
                attempt += 1
                continue
            if status >= 400:
                raise _base.error_from_response(response)
            return response

    def close(self) -> None:
        """Close the underlying HTTP client (unless you passed your own)."""
        if self._owns_http_client:
            self._http.close()

    def __enter__(self) -> Midwater:
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.close()


# =========================================================================== async


class AsyncConversations:
    """``client.conversations`` on ``AsyncMidwater``."""

    def __init__(self, client: AsyncMidwater) -> None:
        self._client = client

    async def create(
        self, conversation: ConversationPayload, idempotency_key: Optional[str] = None
    ) -> ConversationAccepted:
        """Send a finished conversation. See ``Midwater.conversations.create``."""
        key = idempotency_key if idempotency_key is not None else str(uuid.uuid4())
        response = await self._client._request(
            "POST", "/v1/conversations", body=conversation, idempotency_key=key, retryable=True
        )
        return ConversationAccepted.from_dict(
            _base.parse_json(response), replayed=_base.is_replayed(response), idempotency_key=key
        )

    async def get(self, id: str) -> Conversation:
        """Get a conversation by Midwater's ID or your ``external_id``."""
        path = f"/v1/conversations/{_base.path_segment(id)}"
        response = await self._client._request("GET", path, retryable=True)
        return Conversation.from_dict(_base.parse_json(response))

    async def wait(self, id: str, timeout: float = 60.0, interval: float = 1.0) -> Conversation:
        """Poll ``get()`` until ``status`` is ``done`` or ``failed``; see the sync client."""
        _wait_args(timeout, interval)
        deadline = _monotonic() + timeout
        last: Optional[Conversation] = None
        while True:
            last = await self.get(id)
            if last.status in _base.TERMINAL_STATUSES:
                return last
            remaining = deadline - _monotonic()
            if remaining <= 0:
                raise WaitTimeoutError(_timeout_message(id, timeout, last), last)
            await _async_sleep(min(interval, remaining))

    async def feedback(
        self,
        id: str,
        check_key: str,
        verdict: FeedbackValue,
        note: Optional[str] = None,
    ) -> Feedback:
        """Confirm or correct one check's result. Not retried on server errors."""
        path = f"/v1/conversations/{_base.path_segment(id)}/feedback"
        response = await self._client._request(
            "POST", path, body=_feedback_body(check_key, verdict, note), retryable=False
        )
        return Feedback.from_dict(_base.parse_json(response))


class AsyncAgents:
    def __init__(self, client: AsyncMidwater) -> None:
        self._client = client

    async def health(self, agent_id: str) -> AgentHealth:
        """The agent's health in the API key's environment."""
        path = f"/v1/agents/{_base.path_segment(agent_id)}/health"
        response = await self._client._request("GET", path, retryable=True)
        return AgentHealth.from_dict(_base.parse_json(response))


class AsyncGroups:
    def __init__(self, client: AsyncMidwater) -> None:
        self._client = client

    async def health(self, group_id: str) -> GroupHealth:
        """The group's health in the API key's environment."""
        path = f"/v1/groups/{_base.path_segment(group_id)}/health"
        response = await self._client._request("GET", path, retryable=True)
        return GroupHealth.from_dict(_base.parse_json(response))


class AsyncMidwater(_ClientConfig):
    """Asynchronous Midwater API client (``httpx.AsyncClient``). Same options as ``Midwater``."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = _base.DEFAULT_TIMEOUT,
        max_retries: int = _base.DEFAULT_MAX_RETRIES,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(api_key, base_url, timeout, max_retries)
        self._owns_http_client = http_client is None
        self._http = http_client if http_client is not None else httpx.AsyncClient(timeout=timeout)
        self.conversations = AsyncConversations(self)
        self.agents = AsyncAgents(self)
        self.groups = AsyncGroups(self)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        idempotency_key: Optional[str] = None,
        retryable: bool,
    ) -> httpx.Response:
        prepared = self._prepare(method, path, body, idempotency_key)
        attempt = 0
        while True:
            try:
                response = await self._http.request(**prepared)
            except httpx.TransportError as exc:
                if retryable and attempt < self.max_retries:
                    await _async_sleep(_base.retry_delay(attempt))
                    attempt += 1
                    continue
                raise APIConnectionError(f"Could not reach Midwater: {exc}") from exc
            except httpx.RequestError as exc:
                raise APIConnectionError(f"Could not reach Midwater: {exc}") from exc
            status = response.status_code
            if attempt < self.max_retries and _base.should_retry_status(
                status, retryable=retryable
            ):
                await _async_sleep(_base.retry_delay(attempt, response.headers))
                attempt += 1
                continue
            if status >= 400:
                raise _base.error_from_response(response)
            return response

    async def close(self) -> None:
        """Close the underlying HTTP client (unless you passed your own)."""
        if self._owns_http_client:
            await self._http.aclose()

    async def __aenter__(self) -> AsyncMidwater:
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        await self.close()
