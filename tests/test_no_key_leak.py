"""The API key must never reach a log record, stdout/stderr, an exception or ``repr(client)``."""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterator, List

import httpx
import pytest

from helpers import API_KEY, conversation_json, error_json, fixture, json_response
from midwater import (
    APIConnectionError,
    AuthenticationError,
    Midwater,
    MidwaterError,
    ValidationError,
)

LOGGERS = ["", "httpx", "httpcore", "midwater"]
# The secret part of the key, so a partial or reformatted leak is caught too.
SECRET_PART = API_KEY[len("mw_test_") :]


class _Always401(BaseHTTPRequestHandler):
    """A real local server, so httpx and httpcore log a genuine request/response at DEBUG."""

    def _answer(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        body = json.dumps(error_json("authentication_error")).encode()
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _answer
    do_POST = _answer

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture
def local_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Always401)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _chain(exc: BaseException) -> List[BaseException]:
    seen: List[BaseException] = []
    current: Any = exc
    while current is not None and current not in seen:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def _exception_texts(exc: BaseException) -> List[str]:
    texts: List[str] = []
    for item in _chain(exc):
        texts += [str(item), repr(item), str(item.args), str(vars(item))]
        texts += [str(value) for value in vars(item).values()]
        texts += [repr(value) for value in vars(item).values()]
    return texts


def test_api_key_never_logged_printed_or_raised(
    make_client: Callable[..., Any],
    sleeps: List[float],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    local_server: str,
) -> None:
    for name in LOGGERS:
        caplog.set_level(logging.DEBUG, logger=name)

    errors: List[BaseException] = []
    client, rec = make_client(
        [
            json_response(503, error_json("service_unavailable")),  # create: retried
            json_response(202, fixture("conversation-accepted.json")),
            json_response(200, fixture("conversation.json")),  # get
            json_response(200, conversation_json("evaluating")),  # wait
            json_response(200, fixture("conversation.json")),
            json_response(200, fixture("feedback.json")),  # feedback
            json_response(200, fixture("agent-health.json")),
            json_response(200, fixture("group-health.json")),
            json_response(401, error_json("authentication_error")),
            json_response(422, error_json("validation_error")),
            httpx.ConnectError("connection refused"),  # repeats for every retry
        ]
    )
    texts: List[str] = [repr(client), str(client)]

    accepted = client.conversations.create(fixture("conversation-create.json"))
    conv = client.conversations.get(accepted.id)
    done = client.conversations.wait(accepted.id, interval=0.01)
    fb = client.conversations.feedback(conv.id, "need_unresolved", "fail", note="n")
    agent = client.agents.health("front-desk")
    group = client.groups.health("brightsmile-dental")
    texts += [repr(x) for x in (accepted, conv, done, fb, agent, group)]

    with pytest.raises(AuthenticationError) as auth:
        client.conversations.get("c1")
    errors.append(auth.value)
    with pytest.raises(ValidationError) as invalid:
        client.conversations.create({"external_id": "x", "channel": "fax", "transcript": []})
    errors.append(invalid.value)
    with pytest.raises(APIConnectionError) as conn:
        client.conversations.get("c1")
    errors.append(conn.value)
    assert len(rec.requests) == 13  # 2 + 1 + 2 + 1 + 1 + 1 + 1 + 1 + 3

    # Through the real httpx/httpcore stack, against a local server and a closed port.
    with Midwater(api_key=API_KEY, base_url=local_server, max_retries=0) as real:
        texts.append(repr(real))
        with pytest.raises(AuthenticationError) as real_auth:
            real.conversations.feedback("c1", "k", "pass")
        errors.append(real_auth.value)
    with Midwater(api_key=API_KEY, base_url="http://127.0.0.1:1", max_retries=1) as closed:
        with pytest.raises(APIConnectionError) as refused:
            closed.conversations.get("c1")
        errors.append(refused.value)

    # Bad keys are never echoed either.
    with pytest.raises(MidwaterError) as bad_key:
        Midwater(api_key="sk_live_" + SECRET_PART, base_url=local_server)
    errors.append(bad_key.value)

    for exc in errors:
        texts += _exception_texts(exc)
    for record in caplog.records:
        texts += [record.getMessage(), str(record.args), str(vars(record))]
    texts.append(caplog.text)
    out, err = capsys.readouterr()
    texts += [out, err]

    assert any("HTTP Request" in r.getMessage() for r in caplog.records if r.name == "httpx")
    assert any(r.name.startswith("httpcore") for r in caplog.records)
    for text in texts:
        assert API_KEY not in text
        assert SECRET_PART not in text
