from __future__ import annotations

import json
from typing import Any, Dict, Optional

import pytest

import midwater
from helpers import fixture
from midwater import MidwaterError, WebhookVerificationError, verify_webhook, webhooks

VECTORS: Dict[str, Any] = fixture("webhook-vectors.json")
SECRET: str = VECTORS["secret"]
BODY: str = VECTORS["body"]
NOW: int = VECTORS["now"]
TOLERANCE: int = VECTORS["tolerance_seconds"]
CASES: Dict[str, Dict[str, Any]] = {c["name"]: c for c in VECTORS["cases"]}


def _verify(case: Dict[str, Any], secret: str = SECRET) -> Any:
    body: str = case.get("body", BODY)
    headers = {} if case["header"] is None else {"Midwater-Signature": case["header"]}
    return verify_webhook(body, headers, secret, tolerance=TOLERANCE, now=NOW)


def _reason(case: Dict[str, Any], secret: str = SECRET) -> Optional[str]:
    with pytest.raises(WebhookVerificationError) as info:
        _verify(case, secret)
    return info.value.reason


# ----------------------------------------------------------------------------- the five named cases


def test_good_payload() -> None:
    event = _verify(CASES["valid"])
    assert event == json.loads(BODY)
    assert event["type"] == "conversation.evaluated"


def test_tampered_body() -> None:
    assert _reason(CASES["tampered_body"]) == "invalid_signature"


def test_wrong_secret() -> None:
    # The vector's header was signed with a different secret.
    assert _reason(CASES["wrong_secret"]) == "invalid_signature"
    # And the good header fails when we verify with a different secret.
    assert _reason(CASES["valid"], secret="whsec_not_the_right_secret") == "invalid_signature"


def test_stale_timestamp() -> None:
    assert _reason(CASES["stale_timestamp"]) == "stale_timestamp"


def test_two_v1_values() -> None:
    case = CASES["valid_with_rotated_second_v1"]
    assert case["header"].count("v1=") == 2
    assert _verify(case)["id"] == json.loads(BODY)["id"]


# ----------------------------------------------------------------------------- every vector


def test_vectors_cover_every_reason() -> None:
    reasons = {c.get("reason") for c in VECTORS["cases"] if not c["valid"]}
    assert {"missing_header", "malformed_header", "stale_timestamp", "invalid_signature"} <= reasons


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["name"] for c in VECTORS["cases"]])
@pytest.mark.parametrize("as_bytes", [False, True], ids=["str", "bytes"])
def test_vector(case: Dict[str, Any], as_bytes: bool) -> None:
    body: str = case.get("body", BODY)
    payload = body.encode("utf-8") if as_bytes else body
    headers = {} if case["header"] is None else {"Midwater-Signature": case["header"]}
    if case["valid"]:
        event = webhooks.verify(payload, headers, SECRET, tolerance=TOLERANCE, now=NOW)
        assert event == json.loads(body)
    else:
        with pytest.raises(WebhookVerificationError) as info:
            webhooks.verify(payload, headers, SECRET, tolerance=TOLERANCE, now=NOW)
        assert info.value.reason == case["reason"]


def test_only_midwater_signature_header_is_read() -> None:
    assert not hasattr(webhooks, "LEGACY_SIGNATURE_HEADER")
    assert webhooks.__all__.count("SIGNATURE_HEADER") == 1
    with pytest.raises(WebhookVerificationError) as info:
        webhooks.verify(
            BODY, {"X-Signature": CASES["valid"]["header"]}, SECRET, tolerance=TOLERANCE, now=NOW
        )
    assert info.value.reason == "missing_header"


def test_verify_webhook_alias() -> None:
    assert midwater.verify_webhook is webhooks.verify


def test_header_lookup_is_case_insensitive() -> None:
    header = CASES["valid"]["header"]
    for name in ["midwater-signature", "MIDWATER-SIGNATURE", "Midwater-Signature"]:
        webhooks.verify(BODY, {name: header}, SECRET, tolerance=TOLERANCE, now=NOW)


def test_no_secret() -> None:
    header = CASES["valid"]["header"]
    with pytest.raises(WebhookVerificationError) as info:
        webhooks.verify(BODY, {"Midwater-Signature": header}, "", now=NOW)
    assert info.value.reason == "no_secret"
    with pytest.raises(WebhookVerificationError):
        webhooks.sign(BODY, "")


def test_wrong_length_v1_skipped() -> None:
    t, v1 = CASES["valid"]["header"].split(",")
    header = f"{t},v1=abcd,junk,{v1}"
    webhooks.verify(BODY, {"Midwater-Signature": header}, SECRET, tolerance=TOLERANCE, now=NOW)


@pytest.mark.parametrize("header", ["", "garbage", "t=abc,v1=" + "0" * 64, "t=1791493920"])
def test_malformed_or_missing(header: str) -> None:
    with pytest.raises(WebhookVerificationError) as info:
        webhooks.verify(BODY, {"Midwater-Signature": header}, SECRET, now=NOW)
    assert info.value.reason in {"missing_header", "malformed_header"}


def test_sign_matches_vector() -> None:
    assert webhooks.sign(BODY, SECRET, timestamp=1791493920) == CASES["valid"]["header"]


def test_sign_round_trip_with_clock() -> None:
    body = b'{"id":"evt_1","type":"test","environment":"test","created_at":"x","data":{}}'
    header = webhooks.sign(body, SECRET)
    event = webhooks.verify(body, {"Midwater-Signature": header}, SECRET)
    assert event["type"] == "test"


def test_tolerance_boundary() -> None:
    header = webhooks.sign(BODY, SECRET, timestamp=NOW - 300)
    webhooks.verify(BODY, {"Midwater-Signature": header}, SECRET, tolerance=300, now=NOW)
    with pytest.raises(WebhookVerificationError):
        webhooks.verify(BODY, {"Midwater-Signature": header}, SECRET, tolerance=299, now=NOW)


@pytest.mark.parametrize("payload", ["not json", "[1, 2]"])
def test_signed_payload_that_is_not_an_object(payload: str) -> None:
    header = webhooks.sign(payload, SECRET, timestamp=NOW)
    with pytest.raises(MidwaterError) as info:
        webhooks.verify(payload, {"Midwater-Signature": header}, SECRET, now=NOW)
    assert not isinstance(info.value, WebhookVerificationError)
