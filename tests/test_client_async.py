from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, List

import httpx
import pytest

import midwater._client as client_module
from helpers import API_KEY, BASE_URL, conversation_json, error_json, fixture, json_response
from midwater import (
    APIConnectionError,
    APIError,
    AsyncMidwater,
    AuthenticationError,
    IdempotencyConflictError,
    MethodNotAllowedError,
    NotFoundError,
    PayloadTooLargeError,
    PermissionDeniedError,
    RateLimitError,
    RequestTimeoutError,
    ServerError,
    ServiceUnavailableError,
    ValidationError,
)
from midwater._errors import WaitTimeoutError

PAYLOAD: Dict[str, Any] = fixture("conversation-create.json")
ACCEPTED: Dict[str, Any] = fixture("conversation-accepted.json")
FEEDBACK: Dict[str, Any] = fixture("feedback.json")
ERRORS: Dict[str, Any] = fixture("errors.json")
SERVER_ERROR = error_json("server_error")

EXPECTED_CLASS = {
    "authentication_error": AuthenticationError,
    "permission_denied": PermissionDeniedError,
    "invalid_json": ValidationError,
    "validation_error": ValidationError,
    "not_found": NotFoundError,
    "idempotency_conflict": IdempotencyConflictError,
    "method_not_allowed": MethodNotAllowedError,
    "payload_too_large": PayloadTooLargeError,
    "rate_limited": RateLimitError,
    "server_error": ServerError,
    "service_unavailable": ServiceUnavailableError,
}

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
    assert rec.json_body() == fixture("conversation-create.json")
    uuid.UUID(req.headers["idempotency-key"])
    assert accepted.id == ACCEPTED["id"]
    assert accepted.replayed is False
    assert accepted.request_id is None


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


async def test_replayed_and_duplicate(make_async_client: MakeClient) -> None:
    client, _ = make_async_client(
        [
            json_response(202, ACCEPTED, {"Idempotent-Replayed": "true"}),
            json_response(200, fixture("conversation-duplicate.json")),
        ]
    )
    assert (await client.conversations.create(PAYLOAD, idempotency_key="k")).replayed is True
    assert (await client.conversations.create(PAYLOAD)).duplicate is True


async def test_get_url_encoding(make_async_client: MakeClient) -> None:
    client, rec = make_async_client(
        [json_response(200, fixture("conversation.json"), {"Midwater-Request-Id": "req_a"})]
    )
    conv = await client.conversations.get("ext/with space")
    assert rec.requests[0].url.raw_path.decode() == "/v1/conversations/ext%2Fwith%20space"
    assert conv.results[0].verdict == "pass"
    assert conv.request_id == "req_a"


async def test_feedback_and_health(make_async_client: MakeClient) -> None:
    client, rec = make_async_client(
        [
            json_response(200, FEEDBACK),
            json_response(200, fixture("agent-health.json")),
            json_response(200, fixture("group-health.json")),
        ]
    )
    fb = await client.conversations.feedback("c", "need_unresolved", "fail", idempotency_key="f1")
    assert fb.source == "api"
    assert (await client.agents.health("front-desk")).agent_id == "front-desk"
    assert (await client.groups.health("brightsmile-dental")).group_id == "brightsmile-dental"
    assert [r.url.path for r in rec.requests] == [
        "/v1/conversations/c/feedback",
        "/v1/agents/front-desk/health",
        "/v1/groups/brightsmile-dental/health",
    ]
    assert rec.requests[0].headers["idempotency-key"] == "f1"


async def test_feedback_generates_key_and_is_never_retried(
    make_async_client: MakeClient, sleeps: List[float]
) -> None:
    client, rec = make_async_client([json_response(429, error_json("rate_limited"))])
    with pytest.raises(RateLimitError):
        await client.conversations.feedback("c", "k", "pass")
    assert len(rec.requests) == 1
    uuid.UUID(rec.requests[0].headers["idempotency-key"])
    assert sleeps == []


@pytest.mark.parametrize("error_type", sorted(ERRORS))
async def test_error_mapping_from_shared_fixture(
    make_async_client: MakeClient, sleeps: List[float], error_type: str
) -> None:
    entry = ERRORS[error_type]
    client, _ = make_async_client([json_response(entry["status"], entry["body"])], max_retries=0)
    with pytest.raises(APIError) as info:
        await client.conversations.get("c1")
    assert type(info.value) is EXPECTED_CLASS[error_type]
    assert info.value.status == entry["status"]
    assert info.value.request_id == entry["body"]["error"].get("request_id")


@pytest.mark.parametrize(
    ("status", "cls"), [(408, RequestTimeoutError), (418, APIError), (504, ServerError)]
)
async def test_error_mapping_by_status(
    make_async_client: MakeClient, sleeps: List[float], status: int, cls: type
) -> None:
    body = {"error": {"type": "t", "message": "m", "fields": {"a": ["b"]}}}
    client, _ = make_async_client([json_response(status, body)], max_retries=0)
    with pytest.raises(APIError) as info:
        await client.conversations.get("c1")
    assert type(info.value) is cls
    assert info.value.fields == {"a": ["b"]}


async def test_retry_on_503_then_success(
    make_async_client: MakeClient, sleeps: List[float]
) -> None:
    client, rec = make_async_client(
        [
            json_response(503, error_json("service_unavailable")),
            json_response(200, conversation_json()),
        ]
    )
    assert (await client.conversations.get("c1")).status == "done"
    assert len(rec.requests) == 2


async def test_no_retry_on_422(make_async_client: MakeClient, sleeps: List[float]) -> None:
    client, rec = make_async_client([json_response(422, error_json("validation_error"))])
    with pytest.raises(ValidationError):
        await client.conversations.create(PAYLOAD)
    assert len(rec.requests) == 1
    assert sleeps == []


async def test_retry_after(make_async_client: MakeClient, sleeps: List[float]) -> None:
    client, _ = make_async_client(
        [
            json_response(429, error_json("rate_limited"), {"Retry-After": "1.5"}),
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


async def test_non_transport_request_error(
    make_async_client: MakeClient, sleeps: List[float]
) -> None:
    client, rec = make_async_client([httpx.TooManyRedirects("loop")])
    with pytest.raises(APIConnectionError):
        await client.conversations.get("c1")
    assert len(rec.requests) == 1


async def test_wait_polls(make_async_client: MakeClient, sleeps: List[float]) -> None:
    client, rec = make_async_client(
        [json_response(200, conversation_json("queued")), json_response(200, conversation_json())]
    )
    conv = await client.conversations.wait("c1", interval=0.5)
    assert conv.status == "done"
    assert sleeps == [0.5]


async def test_wait_real_sleep(make_async_client: MakeClient) -> None:
    client, _ = make_async_client(
        [json_response(200, conversation_json("queued")), json_response(200, conversation_json())]
    )
    assert (await client.conversations.wait("c1", interval=0.001)).status == "done"


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
    async with AsyncMidwater(api_key=API_KEY, base_url=BASE_URL) as client:
        http = client._http
    assert http.is_closed


async def test_does_not_close_supplied_client(make_async_client: MakeClient) -> None:
    client, _ = make_async_client([json_response(200, conversation_json())])
    await client.close()
    assert not client._http.is_closed
