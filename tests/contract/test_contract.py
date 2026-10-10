"""Live contract tests against a local Midwater stack. Run with ``pytest -m contract``."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Iterator, List, Tuple

import httpx
import pytest

from midwater import (
    AsyncMidwater,
    AuthenticationError,
    IdempotencyConflictError,
    Midwater,
    NotFoundError,
    ValidationError,
)
from midwater.types import Conversation, ConversationCreate

pytestmark = pytest.mark.contract

AGENT_ID = "py-contract-agent"
GROUP_ID = "py-contract-group"
HEALTH_STATUSES = {"healthy", "watch", "at_risk", "not_enough_calls"}
CHECK_RESULT_VALUES = {"pass", "fail", "uncertain", "not_applicable", "met", "not_met"}


def make_payload(external_id: str) -> ConversationCreate:
    return {
        "external_id": external_id,
        "channel": "voice",
        "started_at": "2026-10-05T14:02:11Z",
        "ended_at": "2026-10-05T14:03:20Z",
        "ended_by": "caller",
        "agent": {"id": AGENT_ID, "name": "Python contract agent", "version": "0.1.0"},
        "group": {"id": GROUP_ID, "name": "Python contract group"},
        "transcript": [
            {
                "speaker": "agent",
                "text": "Thanks for calling, this call may be recorded. How can I help?",
                "start_ms": 0,
                "end_ms": 2400,
            },
            {
                "speaker": "user",
                "text": "I need to move my appointment to Thursday.",
                "start_ms": 2900,
                "end_ms": 5100,
                "asr_confidence": 0.93,
            },
            {
                "speaker": "agent",
                "text": "Sure, I have Thursday at 10 AM. Does that work?",
                "start_ms": 5600,
                "end_ms": 8200,
            },
            {
                "speaker": "user",
                "text": "Yes, that's perfect.",
                "start_ms": 8700,
                "end_ms": 9900,
                "asr_confidence": 0.96,
            },
            {
                "speaker": "agent",
                "text": "Great, you're all set for Thursday at 10 AM.",
                "start_ms": 61500,
                "end_ms": 64000,
            },
        ],
        "events": [
            {
                "type": "tool_call",
                "name": "reschedule_appointment",
                "status": "success",
                "at_ms": 61000,
            }
        ],
        "metadata": {"language": "en", "sdk": "python-contract"},
    }


class StatusLog:
    """Records the HTTP status of every response through an httpx event hook."""

    def __init__(self) -> None:
        self.statuses: List[int] = []

    def hook(self, response: httpx.Response) -> None:
        self.statuses.append(response.status_code)


@pytest.fixture(scope="module")
def status_log() -> StatusLog:
    return StatusLog()


@pytest.fixture(scope="module")
def client(contract_env: Tuple[str, str], status_log: StatusLog) -> Iterator[Midwater]:
    base_url, api_key = contract_env
    http = httpx.Client(timeout=30.0, event_hooks={"response": [status_log.hook]})
    with Midwater(api_key=api_key, base_url=base_url, http_client=http) as c:
        yield c
    http.close()


@pytest.fixture(scope="module")
def created(client: Midwater, status_log: StatusLog) -> Dict[str, Any]:
    external_id = f"py-contract-{uuid.uuid4()}"
    key = f"py-contract-key-{uuid.uuid4()}"
    accepted = client.conversations.create(make_payload(external_id), idempotency_key=key)
    return {
        "external_id": external_id,
        "key": key,
        "accepted": accepted,
        "http_status": status_log.statuses[-1],
    }


@pytest.fixture(scope="module")
def scored(client: Midwater, created: Dict[str, Any]) -> Conversation:
    return client.conversations.wait(created["accepted"].id, timeout=90, interval=1.0)


def test_create_is_queued(created: Dict[str, Any]) -> None:
    accepted = created["accepted"]
    assert created["http_status"] == 202
    assert accepted.status == "queued"
    assert accepted.id
    assert accepted.duplicate is False
    assert accepted.replayed is False


def test_same_idempotency_key_replays(client: Midwater, created: Dict[str, Any]) -> None:
    again = client.conversations.create(
        make_payload(created["external_id"]), idempotency_key=created["key"]
    )
    assert again.replayed is True
    assert again.id == created["accepted"].id


def test_same_external_id_is_duplicate(
    client: Midwater, created: Dict[str, Any], status_log: StatusLog
) -> None:
    dup = client.conversations.create(make_payload(created["external_id"]))
    assert status_log.statuses[-1] == 200
    assert dup.duplicate is True
    assert dup.replayed is False
    assert dup.id == created["accepted"].id
    assert dup.idempotency_key and dup.idempotency_key != created["key"]


def test_validation_error_fields(client: Midwater) -> None:
    bad: Any = {
        "external_id": f"py-contract-bad-{uuid.uuid4()}",
        "channel": "fax",
        "transcript": [],
    }
    with pytest.raises(ValidationError) as info:
        client.conversations.create(bad)
    err = info.value
    assert err.status == 422
    assert err.type == "validation_error"
    assert "channel" in err.fields
    assert "transcript" in err.fields


def test_get_by_external_id(client: Midwater, created: Dict[str, Any]) -> None:
    conv = client.conversations.get(created["external_id"])
    assert conv.id == created["accepted"].id
    assert conv.external_id == created["external_id"]
    assert conv.channel == "voice"
    assert conv.agent.id == AGENT_ID
    assert conv.status in {"queued", "evaluating", "done", "failed"}
    assert conv.dashboard_url.startswith("http")
    assert conv.transcript[0]["speaker"] == "agent"
    assert conv.events[0]["name"] == "reschedule_appointment"


def test_wait_until_done(scored: Conversation) -> None:
    assert scored.status == "done"
    assert "outcome" in scored.raw
    assert scored.outcome in {None, "resolved", "unresolved", "escalated", "not_real_inquiry"}
    assert isinstance(scored.results, list)
    assert scored.results, "a scored conversation should have at least one check result"
    for result in scored.results:
        assert result.check_key
        assert result.verdict in CHECK_RESULT_VALUES
        assert result.scorer_version is None or isinstance(result.scorer_version, str)
        for key in ("check_key", "verdict", "scorer_version"):
            assert key in result.raw


def test_feedback(client: Midwater, scored: Conversation) -> None:
    check_key = scored.results[0].check_key
    fb = client.conversations.feedback(scored.id, check_key, "pass", note="Python contract test")
    assert fb.source == "api"
    assert fb.check_key == check_key
    assert fb.verdict == "pass"
    assert fb.id


def test_feedback_idempotency(client: Midwater, scored: Conversation) -> None:
    """API 1.2.0: the same key replays; the same key with a different body is a conflict."""
    check_key = scored.results[0].check_key
    key = f"py-fb-{uuid.uuid4()}"
    first = client.conversations.feedback(scored.id, check_key, "fail", idempotency_key=key)
    again = client.conversations.feedback(scored.id, check_key, "fail", idempotency_key=key)
    assert again.replayed is True
    assert again.id == first.id
    with pytest.raises(IdempotencyConflictError):
        client.conversations.feedback(scored.id, check_key, "pass", idempotency_key=key)


def test_create_key_reused_for_different_body_conflicts(
    client: Midwater, created: Dict[str, Any]
) -> None:
    with pytest.raises(IdempotencyConflictError):
        client.conversations.create(
            make_payload(f"{created['external_id']}-other"), idempotency_key=created["key"]
        )


def test_feedback_unknown_check_is_not_found(client: Midwater, scored: Conversation) -> None:
    with pytest.raises(NotFoundError):
        client.conversations.feedback(scored.id, "no_such_check_py_contract", "fail")


def test_not_found(client: Midwater) -> None:
    with pytest.raises(NotFoundError) as info:
        client.conversations.get(f"py-contract-missing-{uuid.uuid4()}")
    assert info.value.status == 404
    assert info.value.type == "not_found"


def test_external_id_needing_url_encoding(client: Midwater) -> None:
    external_id = f"py-contract:{uuid.uuid4()} with space"
    accepted = client.conversations.create(make_payload(external_id))
    conv = client.conversations.get(external_id)
    assert conv.id == accepted.id
    assert conv.external_id == external_id


def test_agent_health(client: Midwater, scored: Conversation) -> None:
    health = client.agents.health(AGENT_ID)
    assert health.agent_id == AGENT_ID
    assert health.environment == "test"
    assert health.health_status in HEALTH_STATUSES
    assert health.reason
    assert health.last_7_days.conversations >= 1
    assert health.last_30_days.conversations >= health.last_7_days.conversations


def test_group_health(client: Midwater, scored: Conversation) -> None:
    health = client.groups.health(GROUP_ID)
    assert health.group_id == GROUP_ID
    assert health.environment == "test"
    assert health.health_status in HEALTH_STATUSES
    assert AGENT_ID in [a.id for a in health.agents]


def test_wrong_key_is_authentication_error(contract_env: Tuple[str, str]) -> None:
    base_url, _ = contract_env
    with Midwater(api_key="mw_test_" + "x" * 32, base_url=base_url) as bad:
        with pytest.raises(AuthenticationError) as info:
            bad.conversations.get("anything")
    assert info.value.status == 401
    assert info.value.type == "authentication_error"


async def test_async_create_get_wait(contract_env: Tuple[str, str]) -> None:
    base_url, api_key = contract_env
    external_id = f"py-contract-{uuid.uuid4()}"
    async with AsyncMidwater(api_key=api_key, base_url=base_url) as aclient:
        accepted = await aclient.conversations.create(make_payload(external_id))
        assert accepted.status == "queued"
        assert accepted.replayed is False
        replay = await aclient.conversations.create(
            make_payload(external_id), idempotency_key=accepted.idempotency_key
        )
        assert replay.replayed is True and replay.id == accepted.id
        conv = await aclient.conversations.get(external_id)
        assert conv.id == accepted.id
        done = await aclient.conversations.wait(accepted.id, timeout=90)
        assert done.status == "done"
        assert done.results
        health = await aclient.agents.health(AGENT_ID)
        assert health.environment == "test"
        with pytest.raises(NotFoundError):
            await aclient.conversations.get(f"py-contract-missing-{uuid.uuid4()}")
