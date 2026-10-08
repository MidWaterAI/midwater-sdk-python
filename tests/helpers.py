"""Shared test helpers (imported by the test modules and conftest)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

API_KEY = "mw_test_" + "a" * 32
BASE_URL = "https://midwater.example"

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "fixtures"

_cache: Dict[str, Any] = {}


def fixture(name: str) -> Any:
    """A deep copy of ``fixtures/<name>`` (the shared fixture set; never edit those files)."""
    if name not in _cache:
        _cache[name] = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    return copy.deepcopy(_cache[name])


def conversation_json(status: str = "done", **overrides: Any) -> Dict[str, Any]:
    """``fixtures/conversation.json``, with ``status`` (and results) adjusted for polling tests."""
    data: Dict[str, Any] = fixture("conversation.json")
    data["status"] = status
    if status != "done":
        data["outcome"] = None
        data["results"] = []
    data.update(overrides)
    return data


def error_json(error_type: str) -> Dict[str, Any]:
    """The body of one entry in ``fixtures/errors.json``."""
    body: Dict[str, Any] = fixture("errors.json")[error_type]["body"]
    return body


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
