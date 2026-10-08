from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, List

import httpx
import pytest

import midwater._client as client_module
from helpers import (
    API_KEY,
    BASE_URL,
    conversation_json,
    error_json,
    fixture,
    json_response,
)
from midwater import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    IdempotencyConflictError,
    Midwater,
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
from midwater.types import AgentHealth, Conversation, ConversationAccepted, GroupHealth

PAYLOAD: Dict[str, Any] = fixture("conversation-create.json")
ACCEPTED: Dict[str, Any] = fixture("conversation-accepted.json")
DUPLICATE: Dict[str, Any] = fixture("conversation-duplicate.json")
FEEDBACK: Dict[str, Any] = fixture("feedback.json")
FEEDBACK_CREATE: Dict[str, Any] = fixture("feedback-create.json")
ERRORS: Dict[str, Any] = fixture("errors.json")
SERVER_ERROR = error_json("server_error")

# Expected exception class for every error type in fixtures/errors.json.
EXPECTED_CLASS = {
    "authentication_error": AuthenticationError,
    "permission_denied": PermissionDeniedError,
    "invalid_json": ValidationError,
    "validation_error": ValidationError,
    "not_found": NotFoundError,
    "idempotency_conflict": IdempotencyConflictError,
    "payload_too_large": PayloadTooLargeError,
    "rate_limited": RateLimitError,
    "server_error": ServerError,
    "service_unavailable": ServiceUnavailableError,
}

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
    assert accepted.request_id is None

    req = rec.requests[0]
    assert req.method == "POST"
    assert str(req.url) == f"{BASE_URL}/v1/conversations"
    assert req.headers["authorization"] == f"Bearer {API_KEY}"
    assert req.headers["accept"] == "application/json"
    assert req.headers["content-type"] == "application/json"
    assert req.headers["user-agent"] == "midwater-python/0.1.0"
    assert rec.json_body() == fixture("conversation-create.json")

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
            json_response(503, error_json("service_unavailable")),
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
    client, rec = make_client([json_response(200, fixture("conversation.json"))])
    conv = client.conversations.get("cmv0187nm005po3016rag4dcg")
    req = rec.requests[0]
    assert req.method == "GET"
    assert req.url.path == "/v1/conversations/cmv0187nm005po3016rag4dcg"
    assert "content-type" not in req.headers
    assert "idempotency-key" not in req.headers
    assert req.content == b""

    assert isinstance(conv, Conversation)
    assert conv.external_id == "call_8f2a91"
    assert conv.outcome == "resolved"
    assert conv.agent.id == "front-desk"
    assert conv.agent.version == "1.0.0"
    assert conv.group is None
    assert conv.transcript[1]["asr_confidence"] == 0.93
    assert conv.events[0]["name"] == "reschedule_appointment"
    result = conv.results[0]
    assert result.check_key == "appointment_not_completed"
    assert result.verdict == "pass"
    assert result.decided_by == "rule"
    assert result.score == 0.0
    assert result.choice is None
    assert result.latency_ms == 0
    assert result.scorer_version == "2026-10-01.1"
    assert conv.raw["id"] == conv.id
    assert conv.request_id is None


def test_success_request_id_from_header(make_client: MakeClient) -> None:
    client, _ = make_client(
        [json_response(200, fixture("conversation.json"), {"Midwater-Request-Id": "req_1"})]
    )
    assert client.conversations.get("c1").request_id == "req_1"


def test_unknown_enum_values_and_fields_pass_through(make_client: MakeClient) -> None:
    body = conversation_json(outcome="callback_booked", brand_new_field=1)
    body["status"] = "archived"
    body["results"][0]["verdict"] = "partially_met"
    client, _ = make_client([json_response(200, body)])
    conv = client.conversations.get("c1")
    assert conv.status == "archived"
    assert conv.outcome == "callback_booked"
    assert conv.results[0].verdict == "partially_met"
    assert conv.raw["brand_new_field"] == 1


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
    client, rec = make_client([json_response(200, FEEDBACK)])
    fb = client.conversations.feedback(
        "ext/1",
        FEEDBACK_CREATE["check_key"],
        FEEDBACK_CREATE["verdict"],
        note=FEEDBACK_CREATE["note"],
    )
    req = rec.requests[0]
    assert req.method == "POST"
    assert req.url.raw_path.decode() == "/v1/conversations/ext%2F1/feedback"
    assert req.headers["content-type"] == "application/json"
    assert rec.json_body() == FEEDBACK_CREATE
    key = req.headers["idempotency-key"]
    assert str(uuid.UUID(key)) == key
    assert fb.id == FEEDBACK["id"]
    assert fb.source == "api"
    assert fb.verdict == "fail"
    assert fb.request_id is None


def test_feedback_uses_given_idempotency_key(make_client: MakeClient) -> None:
    client, rec = make_client([json_response(200, FEEDBACK)])
    client.conversations.feedback("c1", "k", "fail", idempotency_key="fb-1")
    assert rec.requests[0].headers["idempotency-key"] == "fb-1"


def test_feedback_omits_note_when_none(make_client: MakeClient) -> None:
    client, rec = make_client([json_response(200, FEEDBACK)])
    client.conversations.feedback("c1", "k", "pass")
    assert rec.json_body() == {"check_key": "k", "verdict": "pass"}


def test_agent_and_group_health(make_client: MakeClient) -> None:
    client, rec = make_client(
        [
            json_response(200, fixture("agent-health.json")),
            json_response(200, fixture("group-health.json")),
        ]
    )
    a = client.agents.health("front-desk")
    g = client.groups.health("brightsmile-dental")
    assert rec.requests[0].url.path == "/v1/agents/front-desk/health"
    assert rec.requests[1].url.path == "/v1/groups/brightsmile-dental/health"
    assert isinstance(a, AgentHealth)
    assert a.environment == "live"
    assert a.health_status == "watch"
    assert a.group is not None and a.group.id == "brightsmile-dental"
    assert a.last_7_days.resolution_rate == 0.81
    assert a.last_30_days.compliance_failures == 1
    assert isinstance(g, GroupHealth)
    assert [agent.id for agent in g.agents] == ["front-desk", "after-hours"]
    assert g.last_30_days.conversations == 1270
    assert a.request_id is None and g.request_id is None


# ----------------------------------------------------------------------------- replay / duplicate


def test_replayed_header(make_client: MakeClient) -> None:
    client, _ = make_client([json_response(202, ACCEPTED, {"Idempotent-Replayed": "true"})])
    assert client.conversations.create(PAYLOAD, idempotency_key="k").replayed is True


def test_duplicate_flag(make_client: MakeClient) -> None:
    client, _ = make_client([json_response(200, DUPLICATE)])
    accepted = client.conversations.create(PAYLOAD)
    assert accepted.duplicate is True
    assert accepted.replayed is False
    assert accepted.status == "done"


# ----------------------------------------------------------------------------- error mapping


@pytest.mark.parametrize("error_type", sorted(ERRORS))
def test_error_mapping_from_shared_fixture(
    make_client: MakeClient, sleeps: List[float], error_type: str
) -> None:
    entry = ERRORS[error_type]
    status, body = entry["status"], entry["body"]
    client, rec = make_client([json_response(status, body)], max_retries=0)
    with pytest.raises(APIError) as info:
        client.conversations.get("c1")
    err = info.value
    assert type(err) is EXPECTED_CLASS[error_type]
    assert err.status == status
    assert err.type == error_type
    assert err.message == body["error"]["message"]
    assert err.body == body
    assert err.request_id == body["error"].get("request_id")
    if err.request_id:
        assert err.request_id in str(err)
    expected_fields = body["error"].get("fields", {})
    assert err.fields == expected_fields
    assert len(rec.requests) == 1
    assert API_KEY not in str(err) and API_KEY not in repr(err)


def test_every_fixture_error_type_has_an_expected_class() -> None:
    assert set(ERRORS) == set(EXPECTED_CLASS)


@pytest.mark.parametrize(
    ("status", "cls"),
    [
        (408, RequestTimeoutError),
        (418, APIError),
        (402, APIError),
        (501, ServerError),
        (502, ServerError),
        (504, ServerError),
        (599, ServerError),
        (503, ServiceUnavailableError),
    ],
)
def test_error_mapping_by_status_with_unknown_type(
    make_client: MakeClient, sleeps: List[float], status: int, cls: type
) -> None:
    body = {"error": {"type": "brand_new_error_type", "message": "Something specific"}}
    client, _ = make_client([json_response(status, body)], max_retries=0)
    with pytest.raises(APIError) as info:
        client.conversations.get("c1")
    assert type(info.value) is cls
    assert info.value.type == "brand_new_error_type"
    assert info.value.request_id is None


def test_service_unavailable_is_a_server_error() -> None:
    assert issubclass(ServiceUnavailableError, ServerError)


def test_error_request_id_from_header(make_client: MakeClient) -> None:
    client, _ = make_client(
        [json_response(404, error_json("not_found"), {"Midwater-Request-Id": "req_hdr"})]
    )
    with pytest.raises(NotFoundError) as info:
        client.conversations.get("c1")
    assert info.value.request_id == "req_hdr"
    assert "req_hdr" in repr(info.value)


def test_error_body_request_id_wins_over_header(make_client: MakeClient) -> None:
    client, _ = make_client(
        [json_response(403, error_json("permission_denied"), {"Midwater-Request-Id": "req_hdr"})]
    )
    with pytest.raises(PermissionDeniedError) as info:
        client.conversations.get("c1")
    assert info.value.request_id == "req_8Jx2kQ4mT9"


def test_validation_error_fields(make_client: MakeClient) -> None:
    client, _ = make_client([json_response(422, error_json("validation_error"))])
    with pytest.raises(ValidationError) as info:
        client.conversations.create({"external_id": "x", "channel": "fax", "transcript": []})
    assert set(info.value.fields) == {"channel", "transcript"}
    assert info.value.fields["transcript"] == ["transcript needs at least one turn"]


def test_odd_fields_are_normalised(make_client: MakeClient) -> None:
    body = {
        "error": {"type": "validation_error", "message": "bad", "fields": {"a": "one", "b": None}}
    }
    client, _ = make_client([json_response(422, body)])
    with pytest.raises(ValidationError) as info:
        client.conversations.get("c1")
    assert info.value.fields == {"a": ["one"]}


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


def test_non_object_success_body(make_client: MakeClient) -> None:
    client, _ = make_client([json_response(200, [1, 2])])
    with pytest.raises(APIError) as info:
        client.conversations.get("c1")
    assert "isn't an object" in info.value.message


def test_connection_error(make_client: MakeClient, sleeps: List[float]) -> None:
    client, rec = make_client([httpx.ConnectError("refused")])
    with pytest.raises(APIConnectionError):
        client.conversations.get("c1")
    assert len(rec.requests) == 3  # 1 + 2 retries
    assert len(sleeps) == 2


def test_non_transport_request_error_not_retried(
    make_client: MakeClient, sleeps: List[float]
) -> None:
    client, rec = make_client([httpx.TooManyRedirects("loop")])
    with pytest.raises(APIConnectionError):
        client.conversations.get("c1")
    assert len(rec.requests) == 1


# ----------------------------------------------------------------------------- retries


def test_retry_on_503_then_success(make_client: MakeClient, sleeps: List[float]) -> None:
    client, rec = make_client(
        [
            json_response(503, error_json("service_unavailable")),
            json_response(200, conversation_json()),
        ]
    )
    conv = client.conversations.get("c1")
    assert conv.status == "done"
    assert len(rec.requests) == 2
    assert len(sleeps) == 1
    assert 0.5 <= sleeps[0] <= 0.625


@pytest.mark.parametrize("status", [408, 429, 500, 501, 502, 503, 504, 599])
def test_retry_statuses(make_client: MakeClient, sleeps: List[float], status: int) -> None:
    client, rec = make_client(
        [json_response(status, SERVER_ERROR), json_response(200, conversation_json())]
    )
    client.conversations.get("c1")
    assert len(rec.requests) == 2


def test_backoff_grows_and_gives_up(make_client: MakeClient, sleeps: List[float]) -> None:
    client, rec = make_client([json_response(500, SERVER_ERROR)], max_retries=3)
    with pytest.raises(ServerError):
        client.conversations.get("c1")
    assert len(rec.requests) == 4
    assert len(sleeps) == 3
    for attempt, delay in enumerate(sleeps):
        base = 0.5 * 2**attempt
        assert base <= delay <= base * 1.25


def test_max_retries_clamped_to_3(make_client: MakeClient, sleeps: List[float]) -> None:
    client, rec = make_client([json_response(503, SERVER_ERROR)], max_retries=10)
    assert client.max_retries == 3
    with pytest.raises(ServiceUnavailableError):
        client.conversations.get("c1")
    assert len(rec.requests) == 4


def test_backoff_capped_at_8s() -> None:
    from midwater import _base

    assert _base.retry_delay(10) == 8.0


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 413, 418, 422])
def test_no_retry_on_other_4xx(make_client: MakeClient, sleeps: List[float], status: int) -> None:
    client, rec = make_client([json_response(status, {"error": {"type": "x", "message": "y"}})])
    with pytest.raises(APIError):
        client.conversations.create(PAYLOAD)
    assert len(rec.requests) == 1
    assert sleeps == []


def test_retry_after_honoured(make_client: MakeClient, sleeps: List[float]) -> None:
    client, rec = make_client(
        [
            json_response(429, error_json("rate_limited"), {"Retry-After": "3"}),
            json_response(200, conversation_json()),
        ]
    )
    client.conversations.get("c1")
    assert sleeps == [3.0]
    assert len(rec.requests) == 2


def test_retry_after_capped_at_60s(make_client: MakeClient, sleeps: List[float]) -> None:
    client, _ = make_client(
        [
            json_response(503, SERVER_ERROR, {"Retry-After": "3600"}),
            json_response(200, conversation_json()),
        ]
    )
    client.conversations.get("c1")
    assert sleeps == [60.0]


@pytest.mark.parametrize("value", ["Wed, 21 Oct 2026 07:28:00 GMT", "-5"])
def test_unusable_retry_after_falls_back(
    make_client: MakeClient, sleeps: List[float], value: str
) -> None:
    client, _ = make_client(
        [
            json_response(503, SERVER_ERROR, {"Retry-After": value}),
            json_response(200, conversation_json()),
        ]
    )
    client.conversations.get("c1")
    assert 0.5 <= sleeps[0] <= 0.625


def test_max_retries_zero(make_client: MakeClient, sleeps: List[float]) -> None:
    client, rec = make_client([json_response(503, SERVER_ERROR)], max_retries=0)
    with pytest.raises(ServerError):
        client.conversations.get("c1")
    assert len(rec.requests) == 1


@pytest.mark.parametrize("status", [408, 429, 500, 503])
def test_feedback_never_retried(make_client: MakeClient, sleeps: List[float], status: int) -> None:
    client, rec = make_client([json_response(status, SERVER_ERROR)])
    with pytest.raises(APIError):
        client.conversations.feedback("c1", "k", "pass")
    assert len(rec.requests) == 1
    assert sleeps == []


def test_feedback_not_retried_on_connection_error(
    make_client: MakeClient, sleeps: List[float]
) -> None:
    client, rec = make_client([httpx.ConnectError("refused")])
    with pytest.raises(APIConnectionError):
        client.conversations.feedback("c1", "k", "pass")
    assert len(rec.requests) == 1


def test_health_retried(make_client: MakeClient, sleeps: List[float]) -> None:
    client, rec = make_client(
        [
            httpx.ReadTimeout("slow"),
            json_response(200, fixture("agent-health.json")),
            json_response(502, SERVER_ERROR),
            json_response(200, fixture("group-health.json")),
        ]
    )
    client.agents.health("front-desk")
    client.groups.health("brightsmile-dental")
    assert len(rec.requests) == 4


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
    assert "last status: queued" in str(info.value)
    assert len(rec.requests) == 4  # t=0, 2, 4, 5
    assert clock[0] == 1005.0


def test_wait_real_clock_timeout(make_client: MakeClient) -> None:
    client, _ = make_client([json_response(200, conversation_json("evaluating"))])
    with pytest.raises(WaitTimeoutError):
        client.conversations.wait("c1", timeout=0.05, interval=0.01)


@pytest.mark.parametrize(("timeout", "interval"), [(60, 0), (-1, 1)])
def test_wait_rejects_bad_args(make_client: MakeClient, timeout: float, interval: float) -> None:
    client, _ = make_client([json_response(200, conversation_json())])
    with pytest.raises(ValueError):
        client.conversations.wait("c1", timeout=timeout, interval=interval)


def test_real_sleep_helper_is_used(make_client: MakeClient) -> None:
    client, _ = make_client(
        [json_response(200, conversation_json("queued")), json_response(200, conversation_json())]
    )
    assert client.conversations.wait("c1", interval=0.001).status == "done"


# ----------------------------------------------------------------------------- lifecycle


def test_context_manager_closes_owned_client() -> None:
    with Midwater(api_key=API_KEY, base_url=BASE_URL) as client:
        http = client._http
    assert http.is_closed


def test_does_not_close_supplied_client(make_client: MakeClient) -> None:
    client, _ = make_client([json_response(200, conversation_json())])
    client.close()
    assert not client._http.is_closed
