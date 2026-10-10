from __future__ import annotations

import pytest

import midwater
from helpers import API_KEY, BASE_URL, fixture
from midwater import AsyncMidwater, AuthenticationError, Midwater, MidwaterError


def test_public_api_exports() -> None:
    for name in [
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
        "PayloadTooLargeError",
        "RateLimitError",
        "ServerError",
        "ServiceUnavailableError",
        "APIConnectionError",
        "WebhookVerificationError",
        "WaitTimeoutError",
        "webhooks",
        "verify_webhook",
    ]:
        assert hasattr(midwater, name), name
        assert name in midwater.__all__, name
    assert midwater.__version__ == "0.1.0"
    assert not hasattr(midwater, "DEFAULT_BASE_URL")
    assert "DEFAULT_BASE_URL" not in midwater.__all__
    assert midwater.verify_webhook is midwater.webhooks.verify


def test_error_hierarchy() -> None:
    for cls in [
        midwater.AuthenticationError,
        midwater.PermissionDeniedError,
        midwater.ValidationError,
        midwater.NotFoundError,
        midwater.RequestTimeoutError,
        midwater.IdempotencyConflictError,
        midwater.PayloadTooLargeError,
        midwater.RateLimitError,
        midwater.ServerError,
        midwater.ServiceUnavailableError,
    ]:
        assert issubclass(cls, midwater.APIError)
    assert issubclass(midwater.ServiceUnavailableError, midwater.ServerError)
    assert issubclass(midwater.APIError, midwater.MidwaterError)
    assert issubclass(midwater.APIConnectionError, midwater.MidwaterError)
    assert issubclass(midwater.WebhookVerificationError, midwater.MidwaterError)
    assert issubclass(midwater.WaitTimeoutError, midwater.MidwaterError)
    assert issubclass(midwater.WaitTimeoutError, TimeoutError)


def test_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIDWATER_API_KEY", API_KEY)
    monkeypatch.setenv("MIDWATER_BASE_URL", "http://localhost:3200/")
    client = Midwater()
    assert client.base_url == "http://localhost:3200"
    assert client._api_key == API_KEY
    assert client.max_retries == 2
    assert client.timeout == 30.0
    client.close()


@pytest.mark.parametrize("cls", [Midwater, AsyncMidwater])
def test_base_url_is_required(cls: type, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIDWATER_API_KEY", API_KEY)
    with pytest.raises(MidwaterError) as info:
        cls()
    assert str(info.value) == (
        "Set MIDWATER_BASE_URL or pass base_url: the base URL for your Midwater environment"
    )


def test_empty_env_base_url_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIDWATER_BASE_URL", "")
    with pytest.raises(MidwaterError):
        Midwater(api_key=API_KEY)


def test_explicit_args_beat_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIDWATER_API_KEY", "mw_live_" + "z" * 32)
    monkeypatch.setenv("MIDWATER_BASE_URL", "http://env.example")
    client = Midwater(api_key=API_KEY, base_url="http://arg.example")
    assert client._api_key == API_KEY
    assert client.base_url == "http://arg.example"
    client.close()


@pytest.mark.parametrize("cls", [Midwater, AsyncMidwater])
def test_missing_key(cls: type) -> None:
    with pytest.raises(AuthenticationError) as info:
        cls(base_url=BASE_URL)
    assert "Set MIDWATER_API_KEY or pass api_key" in str(info.value)
    assert info.value.status is None


def test_empty_env_key_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIDWATER_API_KEY", "")
    with pytest.raises(AuthenticationError):
        Midwater(base_url=BASE_URL)


@pytest.mark.parametrize("key", ["mw_test_abc", "mw_live_abc", "vk_test_abc", "vk_live_abc"])
def test_valid_key_prefixes(key: str) -> None:
    Midwater(api_key=key, base_url=BASE_URL).close()


@pytest.mark.parametrize(
    "key", ["sk_test_secretvalue", "mw_prod_secretvalue", "MW_TEST_secretvalue", "secretvalue"]
)
@pytest.mark.parametrize("cls", [Midwater, AsyncMidwater])
def test_invalid_key_rejected_without_echo(cls: type, key: str) -> None:
    with pytest.raises(AuthenticationError) as info:
        cls(api_key=key, base_url=BASE_URL)
    assert "secretvalue" not in str(info.value)
    assert "secretvalue" not in repr(info.value)
    assert "mw_test_" in str(info.value)


def test_repr_hides_key() -> None:
    client = Midwater(api_key=API_KEY, base_url="http://localhost:3200")
    text = repr(client)
    assert API_KEY not in text
    assert "aaaa" not in text
    assert "localhost:3200" in text
    assert API_KEY not in str(client)
    client.close()


async def test_async_repr_hides_key() -> None:
    async with AsyncMidwater(api_key=API_KEY, base_url=BASE_URL) as client:
        assert API_KEY not in repr(client)


@pytest.mark.parametrize("value", [-1, 1.5, True])
def test_bad_max_retries_rejected(value: object) -> None:
    with pytest.raises(ValueError):
        Midwater(api_key=API_KEY, base_url=BASE_URL, max_retries=value)  # type: ignore[arg-type]


@pytest.mark.parametrize(("given", "used"), [(0, 0), (1, 1), (3, 3), (4, 3), (100, 3)])
def test_max_retries_clamped(given: int, used: int) -> None:
    client = Midwater(api_key=API_KEY, base_url=BASE_URL, max_retries=given)
    assert client.max_retries == used
    client.close()


def test_planned_value_renames_are_accepted() -> None:
    """Old and new names both parse until the rename lands (PM review 48, A7)."""
    from typing import get_args

    from midwater.types import Conversation, DecidedBy, Outcome

    outcomes = {a for t in get_args(Outcome) for a in get_args(t)}
    assert {"escalated", "handed_to_person", "not_real_inquiry", "not_customer_call"} <= outcomes
    deciders = {a for t in get_args(DecidedBy) for a in get_args(t)}
    assert {"llm_judge", "second_review"} <= deciders

    raw = fixture("conversation.json")
    for outcome in ("handed_to_person", "not_customer_call", "escalated", "not_real_inquiry"):
        body = {**raw, "outcome": outcome}
        body["results"] = [{**raw["results"][0], "decided_by": "second_review"}]
        conv = Conversation.from_dict(body)
        assert conv.outcome == outcome
        assert conv.results[0].decided_by == "second_review"


def test_scorer_version_may_be_null() -> None:
    """The spec allows null when nothing scored a result; it must not become the string "None"."""
    from midwater.types import CheckResult

    raw = fixture("conversation.json")["results"][0]
    assert CheckResult.from_dict({**raw, "scorer_version": None}).scorer_version is None
    assert CheckResult.from_dict(raw).scorer_version == raw["scorer_version"]


def test_coverage_complete_partial_and_absent() -> None:
    """API 1.1.0: coverage says how much of the transcript was read."""
    from midwater import Coverage
    from midwater.types import Conversation

    raw = fixture("conversation.json")
    conv = Conversation.from_dict(raw)
    assert conv.coverage == Coverage(
        complete=True, skipped_turns=0, skipped_from=None, skipped_to=None
    )

    partial = {
        "complete": False,
        "skipped_turns": 42,
        "skipped_from": 1800000,
        "skipped_to": 4200000,
    }
    conv = Conversation.from_dict({**raw, "coverage": partial})
    assert conv.coverage == Coverage(
        complete=False, skipped_turns=42, skipped_from=1800000, skipped_to=4200000
    )

    untimed = {"complete": False, "skipped_turns": 7, "skipped_from": None, "skipped_to": None}
    assert Conversation.from_dict({**raw, "coverage": untimed}).coverage == Coverage(
        complete=False, skipped_turns=7
    )

    older = {k: v for k, v in raw.items() if k != "coverage"}
    assert Conversation.from_dict(older).coverage is None
