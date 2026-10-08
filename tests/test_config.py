from __future__ import annotations

import pytest

import midwater
from helpers import API_KEY
from midwater import AsyncMidwater, AuthenticationError, Midwater


def test_public_api_exports() -> None:
    for name in [
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
        "WebhookVerificationError",
        "WaitTimeoutError",
        "webhooks",
    ]:
        assert hasattr(midwater, name), name
    assert midwater.__version__ == "0.1.0"
    assert midwater.DEFAULT_BASE_URL == "https://api.midwater.ai"


def test_error_hierarchy() -> None:
    for cls in [
        midwater.AuthenticationError,
        midwater.ValidationError,
        midwater.NotFoundError,
        midwater.RateLimitError,
        midwater.ServerError,
    ]:
        assert issubclass(cls, midwater.APIError)
    assert issubclass(midwater.APIError, midwater.MidwaterError)
    assert issubclass(midwater.APIConnectionError, midwater.MidwaterError)
    assert issubclass(midwater.WebhookVerificationError, midwater.MidwaterError)
    assert issubclass(midwater.WaitTimeoutError, midwater.MidwaterError)
    assert issubclass(midwater.WaitTimeoutError, TimeoutError)


def test_env_var_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIDWATER_API_KEY", API_KEY)
    monkeypatch.setenv("MIDWATER_BASE_URL", "http://localhost:3200/")
    client = Midwater()
    assert client.base_url == "http://localhost:3200"
    assert client._api_key == API_KEY
    client.close()


def test_default_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIDWATER_API_KEY", API_KEY)
    client = Midwater()
    assert client.base_url == "https://api.midwater.ai"
    assert client.max_retries == 2
    assert client.timeout == 30.0
    client.close()


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
        cls()
    assert "Set MIDWATER_API_KEY or pass api_key" in str(info.value)
    assert info.value.status is None


def test_empty_env_key_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIDWATER_API_KEY", "")
    with pytest.raises(AuthenticationError):
        Midwater()


@pytest.mark.parametrize("key", ["mw_test_abc", "mw_live_abc", "vk_test_abc", "vk_live_abc"])
def test_valid_key_prefixes(key: str) -> None:
    Midwater(api_key=key).close()


@pytest.mark.parametrize(
    "key", ["sk_test_secretvalue", "mw_prod_secretvalue", "MW_TEST_secretvalue", "secretvalue"]
)
@pytest.mark.parametrize("cls", [Midwater, AsyncMidwater])
def test_invalid_key_rejected_without_echo(cls: type, key: str) -> None:
    with pytest.raises(AuthenticationError) as info:
        cls(api_key=key)
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
    async with AsyncMidwater(api_key=API_KEY) as client:
        assert API_KEY not in repr(client)


def test_negative_max_retries_rejected() -> None:
    with pytest.raises(ValueError):
        Midwater(api_key=API_KEY, max_retries=-1)
