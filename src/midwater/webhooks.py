"""Verify Midwater webhook deliveries.

Every delivery carries ``Midwater-Signature: t=<unix seconds>,v1=<hex>``. ``v1`` is the
HMAC-SHA256, keyed with your endpoint's whole signing secret (``whsec_...``, UTF-8), of
``"<t>.<raw body>"``. There can be several ``v1`` entries while a secret is being rotated;
any match passes.

Always verify the exact bytes you received. Parsing the JSON and serialising it again changes
the bytes and the signature won't match.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections.abc import Mapping
from typing import List, Optional, Tuple, Union, cast

from ._errors import MidwaterError, WebhookVerificationError
from .types import WebhookEvent

__all__ = [
    "SIGNATURE_HEADER",
    "LEGACY_SIGNATURE_HEADER",
    "EVENT_HEADER",
    "DELIVERY_HEADER",
    "DEFAULT_TOLERANCE",
    "verify",
    "sign",
    "WebhookVerificationError",
]

SIGNATURE_HEADER = "Midwater-Signature"
# Older deliveries were signed under this header name; read only when the new one is absent.
LEGACY_SIGNATURE_HEADER = "Verdict-Signature"
EVENT_HEADER = "Midwater-Event"
DELIVERY_HEADER = "Midwater-Delivery"
DEFAULT_TOLERANCE = 300

_HEX_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")

Payload = Union[str, bytes, bytearray]


def _to_bytes(payload: Payload) -> bytes:
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return bytes(payload)


def _compute(secret: str, timestamp: int, body: bytes) -> str:
    message = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _header(headers: Mapping[str, str], name: str) -> Optional[str]:
    wanted = name.lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == wanted:
            return value
    return None


def _parse(header: str) -> Tuple[int, List[str]]:
    timestamp: Optional[int] = None
    signatures: List[str] = []
    saw_v1 = False
    for part in header.split(","):
        key, sep, value = part.strip().partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if key == "t":
            if not value.isdigit():
                raise WebhookVerificationError("malformed_header", "Signature timestamp is invalid")
            timestamp = int(value)
        elif key == "v1":
            saw_v1 = True
            # Non-hex or wrong-length values are skipped, not fatal.
            if _HEX_SHA256.match(value):
                signatures.append(value.lower())
    if timestamp is None or not saw_v1:
        raise WebhookVerificationError(
            "malformed_header", "Signature header needs t=<timestamp> and v1=<signature>"
        )
    return timestamp, signatures


def verify(
    payload: Payload,
    headers: Mapping[str, str],
    secret: str,
    tolerance: int = DEFAULT_TOLERANCE,
    now: Optional[int] = None,
) -> WebhookEvent:
    """Check a delivery's signature and return the parsed event.

    Args:
        payload: The raw request body exactly as received (``bytes`` preferred).
        headers: The request headers; lookup is case-insensitive.
        secret: The destination's signing secret, including the ``whsec_`` prefix.
        tolerance: Maximum age (or clock skew) of the signature timestamp, in seconds.
        now: The current Unix time in seconds; defaults to the system clock.

    Raises:
        WebhookVerificationError: with ``reason`` ``no_secret``, ``missing_header``,
            ``malformed_header``, ``stale_timestamp`` or ``invalid_signature``.
    """
    if not secret:
        raise WebhookVerificationError("no_secret", "No webhook signing secret was given")

    header = _header(headers, SIGNATURE_HEADER)
    if header is None:
        header = _header(headers, LEGACY_SIGNATURE_HEADER)
    if header is None or not header.strip():
        raise WebhookVerificationError(
            "missing_header", f"The {SIGNATURE_HEADER} header is missing"
        )

    timestamp, signatures = _parse(header)
    body = _to_bytes(payload)

    expected = _compute(secret, timestamp, body)
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise WebhookVerificationError(
            "invalid_signature", "No signature matches the payload and secret"
        )

    current = int(time.time()) if now is None else int(now)
    if abs(current - timestamp) > tolerance:
        raise WebhookVerificationError(
            "stale_timestamp", "The signature timestamp is outside the allowed tolerance"
        )

    try:
        event = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MidwaterError("The webhook payload is signed but isn't valid JSON") from exc
    if not isinstance(event, dict):
        raise MidwaterError("The webhook payload is signed but isn't a JSON object")
    return cast(WebhookEvent, event)


def sign(payload: Payload, secret: str, timestamp: Optional[int] = None) -> str:
    """Build a ``Midwater-Signature`` header value (``t=<ts>,v1=<hex>``), e.g. for tests."""
    if not secret:
        raise WebhookVerificationError("no_secret", "No webhook signing secret was given")
    ts = int(time.time()) if timestamp is None else int(timestamp)
    return f"t={ts},v1={_compute(secret, ts, _to_bytes(payload))}"
