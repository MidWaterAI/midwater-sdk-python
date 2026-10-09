"""Configuration, request building, error mapping and retry policy shared by both clients."""

from __future__ import annotations

import json
import os
import random
import re
from collections.abc import Mapping
from typing import Any, Dict, List, Optional, Type
from urllib.parse import quote

import httpx

from ._errors import (
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
)
from ._version import __version__

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 2
# Retry at most 3 times, whatever the caller asks for.
MAX_RETRIES_CAP = 3

API_KEY_ENV = "MIDWATER_API_KEY"
BASE_URL_ENV = "MIDWATER_BASE_URL"

USER_AGENT = f"midwater-python/{__version__}"

# Test keys start mw_test_, live keys mw_live_. Keys made before October 2026 start vk_.
_API_KEY_PATTERN = re.compile(r"^(mw|vk)_(test|live)_")

REQUEST_ID_HEADER = "Midwater-Request-Id"

# 408, 429 and every 5xx. Retried only on requests that are safe to repeat.
RETRY_STATUSES = frozenset({408, 429, *range(500, 600)})
_BACKOFF_BASE = 0.5
_BACKOFF_CAP = 8.0
_RETRY_AFTER_CAP = 60.0

TERMINAL_STATUSES = frozenset({"done", "failed"})


def resolve_api_key(api_key: Optional[str]) -> str:
    key = api_key if api_key is not None else os.environ.get(API_KEY_ENV)
    if not key:
        raise AuthenticationError(f"No API key found. Set {API_KEY_ENV} or pass api_key.")
    if not _API_KEY_PATTERN.match(key):
        # Never echo the key itself.
        raise AuthenticationError(
            "The API key is not a Midwater key: it should start with mw_test_ or mw_live_ "
            "(older keys: vk_test_ or vk_live_)."
        )
    return key


def resolve_base_url(base_url: Optional[str]) -> str:
    """There is no default host: the base URL comes from the argument or the environment."""
    url = base_url or os.environ.get(BASE_URL_ENV)
    if not url:
        raise MidwaterError(
            f"Set {BASE_URL_ENV} or pass base_url: the base URL for your Midwater environment"
        )
    return url.rstrip("/")


def validate_max_retries(max_retries: int) -> int:
    """Reject negatives; clamp anything above ``MAX_RETRIES_CAP`` to the cap."""
    if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 0:
        raise ValueError("max_retries must be a non-negative integer")
    return min(max_retries, MAX_RETRIES_CAP)


def path_segment(value: str) -> str:
    """URL-encode one path segment (IDs may be your own external_id)."""
    if not isinstance(value, str) or value == "":
        raise ValueError("id must be a non-empty string")
    return quote(value, safe="")


def encode_json(body: Any) -> bytes:
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def build_headers(
    api_key: str, *, has_body: bool, idempotency_key: Optional[str]
) -> Dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if has_body:
        headers["Content-Type"] = "application/json"
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def should_retry_status(status: int, *, retryable: bool) -> bool:
    """408, 429 and 5xx are retried, and only for requests that are safe to repeat."""
    return retryable and status in RETRY_STATUSES


def retry_delay(attempt: int, headers: Optional[httpx.Headers] = None) -> float:
    """Seconds to wait before retry number ``attempt + 1``.

    A numeric ``Retry-After`` header wins when present. Midwater doesn't send one today; this
    keeps the SDK correct if it starts to (or a proxy in front of it does).
    """
    if headers is not None:
        retry_after = headers.get("retry-after")
        if retry_after is not None:
            try:
                seconds = float(retry_after.strip())
            except ValueError:
                seconds = -1.0
            if seconds >= 0:
                return min(seconds, _RETRY_AFTER_CAP)
    base: float = _BACKOFF_BASE * (2.0**attempt)
    jittered = base * (1.0 + 0.25 * random.random())
    return min(jittered, _BACKOFF_CAP)


def header_request_id(response: httpx.Response) -> Optional[str]:
    """The ``Midwater-Request-Id`` response header (planned server-side; ``None`` until then)."""
    value = response.headers.get(REQUEST_ID_HEADER)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def is_replayed(response: httpx.Response) -> bool:
    value = str(response.headers.get("idempotent-replayed", ""))
    return value.strip().lower() == "true"


def parse_json(response: httpx.Response) -> Dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise APIError(
            f"Midwater answered HTTP {response.status_code} with a body that isn't JSON",
            status=response.status_code,
            body=response.text,
        ) from exc
    if not isinstance(data, dict):
        raise APIError(
            f"Midwater answered HTTP {response.status_code} with JSON that isn't an object",
            status=response.status_code,
            body=data,
        )
    return data


_STATUS_ERRORS: Dict[int, Type[APIError]] = {
    400: ValidationError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    405: MethodNotAllowedError,
    408: RequestTimeoutError,
    409: IdempotencyConflictError,
    413: PayloadTooLargeError,
    422: ValidationError,
    429: RateLimitError,
    503: ServiceUnavailableError,
}


def _error_class(status: int) -> Type[APIError]:
    """Pick the exception class from the HTTP status alone, never from the error type."""
    cls = _STATUS_ERRORS.get(status)
    if cls is not None:
        return cls
    if status >= 500:
        return ServerError
    return APIError


def _normalise_fields(raw: Any) -> Dict[str, List[str]]:
    if not isinstance(raw, Mapping):
        return {}
    out: Dict[str, List[str]] = {}
    for key, value in raw.items():
        if isinstance(value, list):
            out[str(key)] = [str(v) for v in value]
        elif value is not None:
            out[str(key)] = [str(value)]
    return out


def error_from_response(response: httpx.Response) -> APIError:
    status = response.status_code
    body: Any
    try:
        body = response.json()
    except ValueError:
        body = response.text

    error_type: Optional[str] = None
    message: Optional[str] = None
    request_id: Optional[str] = None
    fields: Dict[str, List[str]] = {}
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        err = body["error"]
        if isinstance(err.get("type"), str):
            error_type = err["type"]
        if isinstance(err.get("message"), str):
            message = err["message"]
        if isinstance(err.get("request_id"), str) and err["request_id"]:
            request_id = err["request_id"]
        fields = _normalise_fields(err.get("fields"))
    if request_id is None:
        request_id = header_request_id(response)

    if not message:
        reason = response.reason_phrase or "error"
        message = f"Midwater answered HTTP {status} {reason}".rstrip()
        if isinstance(body, str) and body.strip():
            snippet = body.strip()
            message += f": {snippet[:200]}"

    cls = _error_class(status)
    return cls(
        message,
        status=status,
        type=error_type,
        fields=fields,
        body=body,
        request_id=request_id,
    )
