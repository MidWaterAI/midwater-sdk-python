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
    AuthenticationError,
    Midwater,
    NotFoundError,
    RateLimitError,
    ServerError,
    ValidationError,
)
from midwater._errors import WaitTimeoutError
from midwater.types import Conversation, ConversationAccepted, ConversationCreate

PAYLOAD: ConversationCreate = {
    "external_id": "call_8f2a91",
    "channel": "voice",
    "agent": {"id": "front-desk", "name": "Front desk"},
    "transcript": [{"speaker": "agent", "text": "Hello"}],
    "events": [{"type": "tool_call", "name": "reschedule_appointment", "status": "success"}],
}

ACCEPTED = {"id": "cmv0187nm005po3016rag4dcg", "status": "queued"}

MakeClient = Callable[..., Any]


# ----------------------------------------------------------------------------- request shape


def test_create_request_shape(make_client: MakeClient) -> None:
    client, rec = make_client([json_response(202, ACCEPTED)])
    accepted = client.conversations.create(PAYLOAD)

    assert isinstance(accepted, ConversationAccepted)
    assert accepted.id == ACCEPTED["id"]
    assert accepted.status == "queued"
    assert accepted.duplicate is False
    assert accepted.replayed is False

    req = rec.requests[0]
    assert req.method == "POST"
    assert str(req.url) == f"{BASE_URL}/v1/conversations"
    assert req.headers["authorization"] == f"Bearer {API_KEY}"
    assert req.headers["accept"] == "application/json"
    assert req.headers["content-type"] == "application/json"
    assert req.headers["user-agent"] == "midwater-python/0.1.0"
    assert rec.json_body() == PAYLOAD

    key = req.headers["idempotency-key"]
    assert str(uuid.UUID(key)) == key
    assert accepted.idempotency_key == key


def test_create_uses_given_idempotency_key(make_client: MakeClient) -> None:
    client, rec = make_client([json_response(202, ACCEPTED)])
    accepted = client.conversations.create(PAYLOAD, idempotency_key="call_8f2a91")
    assert rec.requests[0].headers["idempotency-key"] == "call_8f2a91"
    assert accepted.idempotency_key == "call_8f2a91"


def test_generated_idempotency_key_reused_across_retries(
    make_client: MakeClient, sleeps: List[float]
) -> None:
    client, rec = make_client(
        [
            json_response(503, {"error": {"type": "server_error", "message": "down"}}),
            httpx.ConnectError("refused"),
            json_response(202, ACCEPTED),
        ]
    )
    accepted = client.conversations.create(PAYLOAD)
    keys = {r.headers["idempotency-key"] for r in rec.requests}
    assert len(rec.requests) == 3
    assert len(keys) == 1
    assert accepted.idempotency_key in keys
    assert len(sleeps) == 2


def test_each_create_call_gets_a_new_key(make_client: MakeClient) -> None:
    client, rec = make_client([json_response(202, ACCEPTED)])
    client.conversations.create(PAYLOAD)
    client.conversations.create(PAYLOAD)
    assert rec.requests[0].headers["idempotency-key"] != rec.requests[1].headers["idempotency-key"]


def test_get_request_has_no_body_headers(make_client: MakeClient) -> None:
    client, rec = make_client([json_response(200, conversation_json())])
    conv = client.conversations.get("cmv0187nm005po3016rag4dcg")
    req = rec.requests[0]
    assert req.method == "GET"
    assert req.url.path == "/v1/conversations/cmv0187nm005po3016rag4dcg"
    assert "content-type" not in req.headers
    assert "idempotency-key" not in req.headers
    assert req.content == b""

    assert isinstance(conv, Conversation)
    assert conv.outcome == "resolved"
    assert conv.agent.id == "front-desk"
    assert conv.group is None
    assert conv.results[0].check_key == "need_unresolved"
    assert conv.results[0].verdict == "pass"
    assert conv.results[0].decided_by == "model"
    assert conv.results[0].scorer_version == "2026-10-06.3"
    assert conv.raw["id"] == conv.id


@pytest.mark.parametrize(
    ("given", "encoded"),
    [
        ("call/8f2a 91", "call%2F8f2a%2091"),
        ("a:b?c#d", "a%3Ab%3Fc%23d"),
        ("ünï", "%C3%BCn%C3%AF"),
    ],
)
def test_ids_are_url_encoded(make_client: MakeClient, given: str, encoded: str) -> None:
    client, rec = make_client([json_response(200, conversation_json())])
    client.conversations.get(given)
    assert rec.requests[0].url.raw_path.decode() == f"/v1/conversations/{encoded}"


def test_empty_id_rejected(make_client: MakeClient) -> None:
    client, rec = make_client([json_response(200, conversation_json())])
    with pytest.raises(ValueError):
        client.conversations.get("")
    assert rec.requests == []


def test_feedback_request(make_client: MakeClient) -> None:
    body = {"id": "lbl_1", "check_key": "need_unresolved", "verdict": "fail", "source": "api"}
    client, rec = make_client([json_response(200, body)])
    fb = client.conversations.feedback("ext/1", "need_unresolved", "fail", note="Hung up")
    req = rec.requests[0]
    assert req.method == "POST"
    assert req.url.raw_path.decode() == "/v1/conversations/ext%2F1/feedback"
    assert req.headers["content-type"] == "application/json"
    assert rec.json_body() == {"check_key": "need_unresolved", "verdict": "fail", "note": "Hung up"}
    assert fb.source == "api"
    assert fb.verdict == "fail"


def test_feedback_omits_note_when_none(make_client: MakeClient) -> None:
    body = {"id": "lbl_1", "check_key": "k", "verdict": "pass", "source": "api"}
    client, rec = make_client([json_response(200, body)])
    client.conversations.feedback("c1", "k", "pass")
    assert rec.json_body() == {"check_key": "k", "verdict": "pass"}


def test_agent_and_group_health(make_client: MakeClient) -> None:
    agent = {
        "agent_id": "front-desk",
        "name": "Front desk",
        "environment": "test",
        "health_status": "not_enough_calls",
        "reason": "Needs 4 more calls with an outcome this week",
        "group": {"id": "clinics", "name": "Clinics"},
        "last_7_days": window_json(),
        "last_30_days": window_json(),
    }
    group = {
        "group_id": "clinics",
        "name": "Clinics",
        "environment": "test",
        "health_status": "healthy",
        "reason": "All agents healthy",
        "agents": [{"id": "front-desk", "name": "Front desk", "health_status": "healthy"}],
        "last_7_days": window_json(),
        "last_30_days": window_json(),
    }
    client, rec = make_client([json_response(200, agent), json_response(200, group)])
    a = client.agents.health("front-desk")
    g = client.groups.health("clinics")
    assert rec.requests[0].url.path == "/v1/agents/front-desk/health"
    assert rec.requests[1].url.path == "/v1/groups/clinics/health"
    assert a.environment == "test"
    assert a.group is not None and a.group.id == "clinics"
    assert a.last_7_days.resolution_rate == 0.5
    assert a.last_7_days.handed_to_person_rate is None
    assert g.agents[0].id == "front-desk"
    assert g.last_30_days.conversations == 3


# ----------------------------------------------------------------------------- replay / duplicate


def test_replayed_header(make_client: MakeClient) -> None:
    client, _ = make_client([json_response(202, ACCEPTED, {"Idempotent-Replayed": "true"})])
    assert client.conversations.create(PAYLOAD, idempotency_key="k").replayed is True


def test_duplicate_flag(make_client: MakeClient) -> None:
    dup = {"id": ACCEPTED["id"], "status": "done", "duplicate": True}
    client, _ = make_client([json_response(200, dup)])
    accepted = client.conversations.create(PAYLOAD)
    assert accepted.duplicate is True
    assert accepted.replayed is False
    assert accepted.status == "done"


# ----------------------------------------------------------------------------- error mapping


@pytest.mark.parametrize(
    ("status", "error_type", "cls"),
    [
        (401, "authentication_error", AuthenticationError),
        (404, "not_found", NotFoundError),
        (400, "invalid_json", ValidationError),
        (422, "validation_error", ValidationError),
        (429, "rate_limited", RateLimitError),
        (500, "server_error", ServerError),
        (502, "server_error", ServerError),
        (501, "not_implemented", ServerError),
        (409, "conflict", APIError),
        (403, "forbidden", APIError),
    ],
)
def test_error_mapping(
    make_client: MakeClient, sleeps: List[float], status: int, error_type: str, cls: type
) -> None:
    body = {"error": {"type": error_type, "message": "Something specific"}}
    client, _ = make_client([json_response(status, body)], max_retries=0)
    with pytest.raises(cls) as info:
        client.conversations.get("c1")
    err = info.value
    assert isinstance(err, APIError)
    assert type(err) is cls
    assert err.status == status
    assert err.type == error_type
    assert err.message == "Something specific"
    assert err.fields == {}
    assert err.body == body
    assert API_KEY not in str(err) and API_KEY not in repr(err)


def test_validation_error_fields(make_client: MakeClient) -> None:
    body = {
        "error": {
            "type": "validation_error",
            "message": "The conversation payload is invalid",
            "fields": {
                "channel": ["Invalid enum value. Expected 'voice' | 'chat', received 'fax'"],
                "transcript": ["transcript needs at least one turn"],
            },
        }
    }
    client, _ = make_client([json_response(422, body)])
    with pytest.raises(ValidationError) as info:
        client.conversations.create({"external_id": "x", "channel": "fax", "transcript": []})
    assert set(info.value.fields) == {"channel", "transcript"}
    assert info.value.fields["transcript"] == ["transcript needs at least one turn"]


def test_non_json_error_body(make_client: MakeClient, sleeps: List[float]) -> None:
    client, _ = make_client([httpx.Response(502, text="<html>Bad gateway</html>")], max_retries=0)
    with pytest.raises(ServerError) as info:
        client.conversations.get("c1")
    err = info.value
    assert err.status == 502
    assert err.type is None
    assert err.body == "<html>Bad gateway</html>"
    assert "502" in err.message


def test_non_json_success_body(make_client: MakeClient) -> None:
    client, _ = make_client([httpx.Response(200, text="not json")])
    with pytest.raises(APIError) as info:
        client.conversations.get("c1")
    assert info.value.status == 200


def test_connection_error(make_client: MakeClient, sleeps: List[float]) -> None:
    client, rec = make_client([httpx.ConnectError("refused")])
    with pytest.raises(APIConnectionError):
        client.conversations.get("c1")
    assert len(rec.requests) == 3  # 1 + 2 retries
    assert len(sleeps) == 2


# ----------------------------------------------------------------------------- retries


def test_retry_on_503_then_success(make_client: MakeClient, sleeps: List[float]) -> None:
    client, rec = make_client(
        [
            json_response(503, {"error": {"type": "server_error", "message": "x"}}),
            json_response(200, conversation_json()),
        ]
    )
    conv = client.conversations.get("c1")
    assert conv.status == "done"
    assert len(rec.requests) == 2
    assert len(sleeps) == 1
    assert 0.5 <= sleeps[0] <= 0.625


def test_backoff_grows_and_gives_up(make_client: MakeClient, sleeps: List[float]) -> None:
    client, rec = make_client(
        [json_response(500, {"error": {"type": "server_error", "message": "x"}})], max_retries=4
    )
    with pytest.raises(ServerError):
        client.conversations.get("c1")
    assert len(rec.requests) == 5
    assert len(sleeps) == 4
    for attempt, delay in enumerate(sleeps):
        base = min(0.5 * 2**attempt, 8.0)
        assert base <= delay <= min(base * 1.25, 8.0)


def test_backoff_capped_at_8s(make_client: MakeClient, sleeps: List[float]) -> None:
    client, _ = make_client(
        [json_response(503, {"error": {"type": "server_error", "message": "x"}})], max_retries=6
    )
    with pytest.raises(ServerError):
        client.conversations.get("c1")
    assert max(sleeps) <= 8.0
    assert sleeps[-1] == 8.0


def test_no_retry_on_422(make_client: MakeClient, sleeps: List[float]) -> None:
    body = {"error": {"type": "validation_error", "message": "bad", "fields": {"_root": ["x"]}}}
    client, rec = make_client([json_response(422, body)])
    with pytest.raises(ValidationError):
        client.conversations.create(PAYLOAD)
    assert len(rec.requests) == 1
    assert sleeps == []


@pytest.mark.parametrize("status", [400, 401, 404, 409])
def test_no_retry_on_other_4xx(make_client: MakeClient, sleeps: List[float], status: int) -> None:
    client, rec = make_client([json_response(status, {"error": {"type": "x", "message": "y"}})])
    with pytest.raises(APIError):
        client.conversations.get("c1")
    assert len(rec.requests) == 1


def test_retry_after_honoured(make_client: MakeClient, sleeps: List[float]) -> None:
    client, rec = make_client(
        [
            json_response(
                429, {"error": {"type": "rate_limited", "message": "slow"}}, {"Retry-After": "3"}
            ),
            json_response(200, conversation_json()),
        ]
    )
    client.conversations.get("c1")
    assert sleeps == [3.0]
    assert len(rec.requests) == 2


def test_non_numeric_retry_after_falls_back(make_client: MakeClient, sleeps: List[float]) -> None:
    client, _ = make_client(
        [
            json_response(
                503,
                {"error": {"type": "server_error", "message": "x"}},
                {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"},
            ),
            json_response(200, conversation_json()),
        ]
    )
    client.conversations.get("c1")
    assert 0.5 <= sleeps[0] <= 0.625


def test_max_retries_zero(make_client: MakeClient, sleeps: List[float]) -> None:
    client, rec = make_client(
        [json_response(503, {"error": {"type": "server_error", "message": "x"}})], max_retries=0
    )
    with pytest.raises(ServerError):
        client.conversations.get("c1")
    assert len(rec.requests) == 1


def test_feedback_not_retried_on_5xx(make_client: MakeClient, sleeps: List[float]) -> None:
    client, rec = make_client(
        [json_response(503, {"error": {"type": "server_error", "message": "x"}})]
    )
    with pytest.raises(ServerError):
        client.conversations.feedback("c1", "k", "pass")
    assert len(rec.requests) == 1


def test_feedback_retried_on_429(make_client: MakeClient, sleeps: List[float]) -> None:
    body = {"id": "lbl_1", "check_key": "k", "verdict": "pass", "source": "api"}
    client, rec = make_client(
        [
            json_response(429, {"error": {"type": "rate_limited", "message": "x"}}),
            json_response(200, body),
        ]
    )
    assert client.conversations.feedback("c1", "k", "pass").id == "lbl_1"
    assert len(rec.requests) == 2


# ----------------------------------------------------------------------------- wait()


def test_wait_polls_until_done(make_client: MakeClient, sleeps: List[float]) -> None:
    client, rec = make_client(
        [
            json_response(200, conversation_json("queued")),
            json_response(200, conversation_json("evaluating")),
            json_response(200, conversation_json("done")),
        ]
    )
    conv = client.conversations.wait("c1", timeout=30, interval=0.25)
    assert conv.status == "done"
    assert len(rec.requests) == 3
    assert sleeps == [0.25, 0.25]


def test_wait_returns_on_failed(make_client: MakeClient, sleeps: List[float]) -> None:
    client, _ = make_client([json_response(200, conversation_json("failed"))])
    assert client.conversations.wait("c1").status == "failed"
    assert sleeps == []


def test_wait_times_out(make_client: MakeClient, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]

    def fake_sleep(seconds: float) -> None:
        clock[0] += seconds

    monkeypatch.setattr(client_module, "_sleep", fake_sleep)
    monkeypatch.setattr(client_module, "_monotonic", lambda: clock[0])
    client, rec = make_client([json_response(200, conversation_json("queued"))])
    with pytest.raises(WaitTimeoutError) as info:
        client.conversations.wait("c1", timeout=5, interval=2)
    assert isinstance(info.value, TimeoutError)
    assert info.value.conversation is not None
    assert info.value.conversation.status == "queued"
    assert len(rec.requests) == 4  # t=0, 2, 4, 5
    assert clock[0] == 1005.0


def test_wait_real_clock_timeout(make_client: MakeClient) -> None:
    client, _ = make_client([json_response(200, conversation_json("evaluating"))])
    with pytest.raises(WaitTimeoutError):
        client.conversations.wait("c1", timeout=0.05, interval=0.01)


def test_wait_rejects_bad_interval(make_client: MakeClient) -> None:
    client, _ = make_client([json_response(200, conversation_json())])
    with pytest.raises(ValueError):
        client.conversations.wait("c1", interval=0)


# ----------------------------------------------------------------------------- lifecycle


def test_context_manager_closes_owned_client() -> None:
    with Midwater(api_key=API_KEY) as client:
        http = client._http
    assert http.is_closed


def test_does_not_close_supplied_client(make_client: MakeClient) -> None:
    client, _ = make_client([json_response(200, conversation_json())])
    client.close()
    assert not client._http.is_closed
