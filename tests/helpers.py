"""Shared test helpers (imported by the test modules and conftest)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx

API_KEY = "mw_test_" + "a" * 32
BASE_URL = "https://midwater.example"


def conversation_json(status: str = "done", **overrides: Any) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "id": "cmv0187nm005po3016rag4dcg",
        "external_id": "call_8f2a91",
        "channel": "voice",
        "status": status,
        "outcome": "resolved" if status == "done" else None,
        "dashboard_url": "https://app.midwater.example/c/cmv0187nm005po3016rag4dcg",
        "started_at": "2026-10-05T14:02:11Z",
        "ended_at": "2026-10-05T14:06:40Z",
        "ended_by": "caller",
        "agent": {"id": "front-desk", "name": "Front desk", "version": "1.0.0"},
        "group": None,
        "transcript": [{"speaker": "agent", "text": "Hello"}],
        "events": [{"type": "tool_call", "name": "reschedule_appointment", "status": "success"}],
        "metadata": {"language": "en"},
        "results": []
        if status != "done"
        else [
            {
                "check_key": "need_unresolved",
                "check_name": "Need unresolved",
                "check_version": 3,
                "check_status": "active",
                "score": 0.04,
                "verdict": "pass",
                "choice": None,
                "decided_by": "model",
                "reason": "The appointment was moved.",
                "evidence_turns": [],
                "scorer_version": "2026-10-06.3",
                "latency_ms": 812,
            }
        ],
    }
    data.update(overrides)
    return data


def window_json() -> Dict[str, Any]:
    return {
        "conversations": 3,
        "with_outcome": 2,
        "resolution_rate": 0.5,
        "handed_to_person_rate": None,
        "requests_for_person_not_honored": 0,
        "avg_frustration": 0.1,
        "compliance_failures": 0,
    }


class Recorder:
    """A scripted ``httpx.MockTransport`` handler that records every request."""

    def __init__(self, responses: List[Any]) -> None:
        self.responses = list(responses)
        self.requests: List[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        if callable(item):
            result: httpx.Response = item(request)
            return result
        return item  # type: ignore[no-any-return]

    def json_body(self, index: int = -1) -> Any:
        return json.loads(self.requests[index].content)


def json_response(
    status: int, body: Any, headers: Optional[Dict[str, str]] = None
) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers)
