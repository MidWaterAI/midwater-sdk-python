from __future__ import annotations

import uuid
from typing import Any, Callable, List

import httpx
import pytest

import midwater._client as client_module
from helpers import API_KEY, BASE_URL, conversation_json, json_response, window_json
from midwater import (
    APIConnectionError,
    APIError,
    AsyncMidwater,
    AuthenticationError,
    NotFoundError,
    RateLimitError,
    ServerError,
    ValidationError,
)
from midwater._errors import WaitTimeoutError
from midwater.types import ConversationCreate

PAYLOAD: ConversationCreate = {
    "external_id": "chat_1",
    "channel": "chat",
    "transcript": [{"speaker": "user", "text": "Hi"}],
}
ACCEPTED = {"id": "cmv0187nm005po3016rag4dcg", "status": "queued"}
SERVER_ERROR = {"error": {"type": "server_error", "message": "x"}}

MakeClient = Callable[..., Any]


async def test_create_request_shape(make_async_client: MakeClient) -> None:
    client, rec = make_async_client([json_response(202, ACCEPTED)])
    accepted = await client.conversations.create(PAYLOAD)
    req = rec.requests[0]
    assert req.method == "POST"
    assert str(req.url) == f"{BASE_URL}/v1/conversations"
    assert req.headers["authorization"] == f"Bearer {API_KEY}"
    assert req.headers["content-type"] == "application/json"
    assert req.headers["accept"] == "application/json"
    assert req.headers["user-agent"] == "midwater-python/0.1.0"
    assert rec.json_body() == PAYLOAD
    uuid.UUID(req.headers["idempotency-key"])
    assert accepted.id == ACCEPTED["id"]
    assert accepted.replayed is False


async def test_idempotency_key_reused_across_retries(
    make_async_client: MakeClient, sleeps: List[float]
) -> None:
    client, rec = make_async_client(
        [json_response(502, SERVER_ERROR), httpx.ReadTimeout("slow"), json_response(202, ACCEPTED)]
    )
    await client.conversations.create(PAYLOAD)
    assert len(rec.requests) == 3
    assert len({r.headers["idempotency-key"] for r in rec.requests}) == 1
    assert len(sleeps) == 2


async def test_replayed(make_async_client: MakeClient) -> None:
    client, _ = make_async_client([json_response(202, ACCEPTED, {"Idempotent-Replayed": "true"})])
    assert (await client.conversations.create(PAYLOAD, idempotency_key="k")).replayed is True


async def test_get_url_encoding(make_async_client: MakeClient) -> None:
    client, rec = make_async_client([json_response(200, conversation_json())])
    conv = await client.conversations.get("ext/with space")
    assert rec.requests[0].url.raw_path.decode() == "/v1/conversations/ext%2Fwith%20space"
    assert conv.results[0].verdict == "pass"


async def test_feedback_and_health(make_async_client: MakeClient) -> None:
    fb = {"id": "lbl", "check_key": "k", "verdict": "pass", "source": "api"}
    agent = {
        "agent_id": "a",
        "name": "A",
        "environment": "test",
        "health_status": "healthy",
        "reason": "ok",
        "group": None,
        "last_7_days": window_json(),
        "last_30_days": window_json(),
    }
    group = {
        "group_id": "g",
        "name": "G",
        "environment": "test",
        "health_status": "healthy",
        "reason": "ok",
        "agents": [],
        "last_7_days": window_json(),
        "last_30_days": window_json(),
    }
    client, rec = make_async_client(
        [json_response(200, fb), json_response(200, agent), json_response(200, group)]
    )
    assert (await client.conversations.feedback("c", "k", "pass")).source == "api"
    assert (await client.agents.health("a")).agent_id == "a"
    assert (await client.groups.health("g")).group_id == "g"
    assert [r.url.path for r in rec.requests] == [
        "/v1/conversations/c/feedback",
        "/v1/agents/a/health",
        "/v1/groups/g/health",
    ]


@pytest.mark.parametrize(
    ("status", "cls"),
    [
        (401, AuthenticationError),
        (404, NotFoundError),
        (400, ValidationError),
        (422, ValidationError),
        (429, RateLimitError),
        (503, ServerError),
        (418, APIError),
    ],
)
async def test_error_mapping(
    make_async_client: MakeClient, sleeps: List[float], status: int, cls: type
) -> None:
    body = {"error": {"type": "t", "message": "m", "fields": {"a": ["b"]}}}
    client, _ = make_async_client([json_response(status, body)], max_retries=0)
    with pytest.raises(cls) as info:
        await client.conversations.get("c1")
    assert type(info.value) is cls
    assert info.value.status == status
    assert info.value.fields == {"a": ["b"]}


async def test_retry_on_503_then_success(
    make_async_client: MakeClient, sleeps: List[float]
) -> None:
    client, rec = make_async_client(
        [json_response(503, SERVER_ERROR), json_response(200, conversation_json())]
    )
    assert (await client.conversations.get("c1")).status == "done"
    assert len(rec.requests) == 2


async def test_no_retry_on_422(make_async_client: MakeClient, sleeps: List[float]) -> None:
    client, rec = make_async_client(
        [json_response(422, {"error": {"type": "validation_error", "message": "bad"}})]
    )
    with pytest.raises(ValidationError):
        await client.conversations.create(PAYLOAD)
    assert len(rec.requests) == 1
    assert sleeps == []


async def test_retry_after(make_async_client: MakeClient, sleeps: List[float]) -> None:
    client, _ = make_async_client(
        [
            json_response(429, {"error": {"type": "r", "message": "m"}}, {"Retry-After": "1.5"}),
            json_response(200, conversation_json()),
        ]
    )
    await client.conversations.get("c1")
    assert sleeps == [1.5]


async def test_connection_error(make_async_client: MakeClient, sleeps: List[float]) -> None:
    client, rec = make_async_client([httpx.ConnectError("refused")], max_retries=1)
    with pytest.raises(APIConnectionError) as info:
        await client.conversations.get("c1")
    assert len(rec.requests) == 2
    assert API_KEY not in str(info.value)


async def test_wait_polls(make_async_client: MakeClient, sleeps: List[float]) -> None:
    client, rec = make_async_client(
        [json_response(200, conversation_json("queued")), json_response(200, conversation_json())]
    )
    conv = await client.conversations.wait("c1", interval=0.5)
    assert conv.status == "done"
    assert sleeps == [0.5]


async def test_wait_timeout(make_async_client: MakeClient, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]

    async def fake_sleep(seconds: float) -> None:
        clock[0] += seconds

    monkeypatch.setattr(client_module, "_async_sleep", fake_sleep)
    monkeypatch.setattr(client_module, "_monotonic", lambda: clock[0])
    client, _ = make_async_client([json_response(200, conversation_json("evaluating"))])
    with pytest.raises(WaitTimeoutError):
        await client.conversations.wait("c1", timeout=3, interval=1)
    assert clock[0] == 3.0


async def test_async_context_manager_closes() -> None:
    async with AsyncMidwater(api_key=API_KEY) as client:
        http = client._http
    assert http.is_closed
