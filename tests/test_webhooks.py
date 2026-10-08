from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from midwater import MidwaterError, WebhookVerificationError, webhooks

VECTORS: Dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "webhook-vectors.json").read_text(encoding="utf-8")
)
SECRET: str = VECTORS["secret"]
BODY: str = VECTORS["body"]
NOW: int = VECTORS["now"]
TOLERANCE: int = VECTORS["tolerance_seconds"]


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
        assert event["type"] == "conversation.evaluated"
    else:
        with pytest.raises(WebhookVerificationError) as info:
            webhooks.verify(payload, headers, SECRET, tolerance=TOLERANCE, now=NOW)
        assert info.value.reason == case["reason"]


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["name"] for c in VECTORS["cases"]])
def test_vector_via_legacy_header(case: Dict[str, Any]) -> None:
    body: str = case.get("body", BODY)
    headers = {} if case["header"] is None else {"Verdict-Signature": case["header"]}
    if case["valid"]:
        webhooks.verify(body, headers, SECRET, tolerance=TOLERANCE, now=NOW)
    else:
        with pytest.raises(WebhookVerificationError) as info:
            webhooks.verify(body, headers, SECRET, tolerance=TOLERANCE, now=NOW)
        assert info.value.reason == case["reason"]


def test_header_lookup_is_case_insensitive() -> None:
    header = VECTORS["cases"][0]["header"]
    for name in ["midwater-signature", "MIDWATER-SIGNATURE", "Midwater-Signature"]:
        webhooks.verify(BODY, {name: header}, SECRET, tolerance=TOLERANCE, now=NOW)


def test_midwater_header_wins_over_legacy() -> None:
    good = VECTORS["cases"][0]["header"]
    bad = "t=1791493920,v1=" + "0" * 64
    webhooks.verify(
        BODY,
        {"Midwater-Signature": good, "Verdict-Signature": bad},
        SECRET,
        tolerance=TOLERANCE,
        now=NOW,
    )
    with pytest.raises(WebhookVerificationError) as info:
        webhooks.verify(
            BODY,
            {"Midwater-Signature": bad, "Verdict-Signature": good},
            SECRET,
            tolerance=TOLERANCE,
            now=NOW,
        )
    assert info.value.reason == "invalid_signature"


def test_no_secret() -> None:
    header = VECTORS["cases"][0]["header"]
    with pytest.raises(WebhookVerificationError) as info:
        webhooks.verify(BODY, {"Midwater-Signature": header}, "", now=NOW)
    assert info.value.reason == "no_secret"


def test_wrong_length_v1_skipped() -> None:
    good = VECTORS["cases"][0]["header"]
    t, v1 = good.split(",")
    header = f"{t},v1=abcd,{v1}"
    webhooks.verify(BODY, {"Midwater-Signature": header}, SECRET, tolerance=TOLERANCE, now=NOW)


@pytest.mark.parametrize("header", ["", "garbage", "t=abc,v1=" + "0" * 64, "t=1791493920"])
def test_malformed_or_missing(header: str) -> None:
    with pytest.raises(WebhookVerificationError) as info:
        webhooks.verify(BODY, {"Midwater-Signature": header}, SECRET, now=NOW)
    assert info.value.reason in {"missing_header", "malformed_header"}


def test_sign_matches_vector() -> None:
    assert webhooks.sign(BODY, SECRET, timestamp=1791493920) == VECTORS["cases"][0]["header"]


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


def test_signed_non_json_payload() -> None:
    header = webhooks.sign("not json", SECRET, timestamp=NOW)
    with pytest.raises(MidwaterError) as info:
        webhooks.verify("not json", {"Midwater-Signature": header}, SECRET, now=NOW)
    assert not isinstance(info.value, WebhookVerificationError)
