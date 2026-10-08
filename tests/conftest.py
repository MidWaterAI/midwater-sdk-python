from __future__ import annotations

from typing import Any, Callable, List

import httpx
import pytest

import midwater._client as client_module
from helpers import API_KEY, BASE_URL, Recorder


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MIDWATER_API_KEY", raising=False)
    monkeypatch.delenv("MIDWATER_BASE_URL", raising=False)


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> List[float]:
    """Replace the SDK's sleeping with a recorder (sync and async)."""
    recorded: List[float] = []

    def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    async def fake_async_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(client_module, "_sleep", fake_sleep)
    monkeypatch.setattr(client_module, "_async_sleep", fake_async_sleep)
    return recorded


@pytest.fixture
def make_client() -> Callable[..., Any]:
    def factory(responses: List[Any], **kwargs: Any) -> Any:
        recorder = Recorder(responses)
        http = httpx.Client(transport=httpx.MockTransport(recorder))
        kwargs.setdefault("api_key", API_KEY)
        kwargs.setdefault("base_url", BASE_URL)
        client = client_module.Midwater(http_client=http, **kwargs)
        return client, recorder

    return factory


@pytest.fixture
def make_async_client() -> Callable[..., Any]:
    def factory(responses: List[Any], **kwargs: Any) -> Any:
        recorder = Recorder(responses)
        http = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
        kwargs.setdefault("api_key", API_KEY)
        kwargs.setdefault("base_url", BASE_URL)
        client = client_module.AsyncMidwater(http_client=http, **kwargs)
        return client, recorder

    return factory
