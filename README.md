# Midwater Python SDK

The official Python client for the [Midwater](https://midwater.ai) API. Midwater checks every
conversation your AI agents have, voice calls and chats. Send each finished conversation with
its transcript; Midwater's model scores it in the background, and you read back the outcome,
the result of every check, and each agent's health.

- Sync (`Midwater`) and async (`AsyncMidwater`) clients built on `httpx`
- Typed request payloads and response models (`py.typed`)
- Automatic retries with backoff, and safe retries for sending conversations
- Webhook signature verification
- Python 3.9+

## Install

```bash
pip install midwater
```

> The package is **not published to PyPI yet**. Until it is, install from a checkout:
> `pip install -e /path/to/midwater-sdk-python`.

## Quickstart

Set your API key (and, for a local stack, the base URL) in the environment:

```bash
export MIDWATER_API_KEY=mw_test_...
export MIDWATER_BASE_URL=http://localhost:3200   # optional; defaults to https://api.midwater.ai
```

```python
from midwater import Midwater

client = Midwater()  # reads MIDWATER_API_KEY and MIDWATER_BASE_URL

accepted = client.conversations.create(
    {
        "external_id": "call_8f2a91",
        "channel": "voice",
        "started_at": "2026-10-05T14:02:11Z",
        "ended_at": "2026-10-05T14:06:40Z",
        "ended_by": "caller",
        "agent": {"id": "front-desk", "name": "Front desk", "version": "1.0.0"},
        "group": {"id": "clinics", "name": "Clinics"},
        "transcript": [
            {
                "speaker": "agent",
                "text": "Thanks for calling. How can I help?",
                "start_ms": 0,
                "end_ms": 2400,
            },
            {
                "speaker": "user",
                "text": "I need to move my appointment to Thursday.",
                "start_ms": 2900,
                "end_ms": 5100,
            },
            {
                "speaker": "agent",
                "text": "Done, you're set for Thursday at 10 AM.",
                "start_ms": 5600,
                "end_ms": 8200,
            },
        ],
        "events": [
            {
                "type": "tool_call",
                "name": "reschedule_appointment",
                "status": "success",
                "at_ms": 5000,
            },
        ],
        "metadata": {"language": "en"},
    }
)
print(accepted.id, accepted.status)  # "cmv0...", "queued"

conversation = client.conversations.wait(accepted.id, timeout=60)
print(
    "Outcome:", conversation.outcome
)  # resolved / unresolved / escalated / not_real_inquiry / None
for result in conversation.results:
    print(f"{result.check_key}: {result.verdict} (score {result.score}, by {result.decided_by})")
print("Open in Midwater:", conversation.dashboard_url)
```

`conversations.get(id)` and `wait(id)` accept Midwater's ID or your own `external_id`.
`wait()` polls until `status` is `done` or `failed` and raises `WaitTimeoutError` (a
`MidwaterError` and a `TimeoutError`) if scoring hasn't finished in time. You can also skip
polling and listen for the `conversation.evaluated` webhook.

Each check result has a `verdict` field, the check's result: `pass` (no problem found), `fail`
(Midwater found this problem), `uncertain` (a person should look), `not_applicable`, or `met` /
`not_met` for gating questions. `decided_by` says how it was reached: `rule` (from the events you
sent), `model` (Midwater's model read the conversation), `llm_judge` (a second review for unclear
conversations) or `human`.

Every response model keeps the decoded JSON in `.raw`, so newly added API fields are available
before the SDK names them.

## Feedback

Confirm or correct a check's result. Each call stores a label with source `api`:

```python
feedback = client.conversations.feedback(
    "call_8f2a91",
    check_key="need_unresolved",
    verdict="fail",
    note="Caller hung up before the booking went through.",
)
print(feedback.source)  # "api"
```

Feedback isn't idempotent (every call adds a label), so the SDK does not retry it after server
errors or connection failures; it only retries a `429`.

## Agent and group health

```python
health = client.agents.health("front-desk")
print(health.health_status, "-", health.reason)  # e.g. not_enough_calls - Needs 4 more calls...
print(health.last_7_days.resolution_rate)

group = client.groups.health("clinics")
for agent in group.agents:
    print(agent.id, agent.health_status)
```

Health is measured per environment: a test key sees test traffic only.

## Webhooks

Midwater signs every delivery with the `Midwater-Signature` header,
`t=<unix seconds>,v1=<hex>`, an HMAC-SHA256 of `"<t>.<raw body>"` keyed with your destination's
signing secret (`whsec_...`). During secret rotation a header can carry several `v1` values;
any match passes. `webhooks.verify()` checks the signature and the timestamp (default tolerance
300 seconds) and returns the parsed event.

**Always pass the exact raw bytes you received.** Don't parse the JSON and serialise it again
first; that changes the bytes and the signature won't match.

Flask:

```python
import os
from flask import Flask, request
from midwater import webhooks, WebhookVerificationError

app = Flask(__name__)
SECRET = os.environ["MIDWATER_WEBHOOK_SECRET"]


@app.post("/midwater/webhooks")
def midwater_webhook():
    try:
        event = webhooks.verify(request.get_data(), request.headers, SECRET)
    except WebhookVerificationError as exc:
        return {"error": exc.reason}, 400
    if event["type"] == "conversation.evaluated":
        print(event["data"]["conversation_id"], event["data"]["outcome"])
    return "", 204
```

FastAPI / Starlette:

```python
from fastapi import FastAPI, HTTPException, Request
from midwater import webhooks, WebhookVerificationError

app = FastAPI()


@app.post("/midwater/webhooks")
async def midwater_webhook(request: Request):
    raw = await request.body()  # the raw bytes, not request.json()
    try:
        event = webhooks.verify(raw, request.headers, SECRET)
    except WebhookVerificationError as exc:
        raise HTTPException(status_code=400, detail=exc.reason)
    ...
    return {"ok": True}
```

`WebhookVerificationError.reason` is one of `missing_header`, `malformed_header`,
`stale_timestamp`, `invalid_signature` or `no_secret`. Header lookup is case-insensitive. Older
deliveries also carry the same signature under the legacy `Verdict-Signature` header; the SDK
reads it only when `Midwater-Signature` is absent. Each delivery also has `Midwater-Event` (the
event type) and `Midwater-Delivery` (the same on every retry of one delivery, useful for
de-duplication). Answer with any 2xx within 5 seconds; anything else is retried.

For your own tests, `webhooks.sign(payload, secret, timestamp=None)` builds a valid header value.

## Errors

All errors derive from `midwater.MidwaterError`.

| Exception | When |
| --- | --- |
| `AuthenticationError` | HTTP 401, or no usable API key at construction |
| `ValidationError` | HTTP 400 (invalid JSON) or 422 (schema); see `.fields` |
| `NotFoundError` | HTTP 404 |
| `RateLimitError` | HTTP 429 (after retries) |
| `ServerError` | HTTP 5xx (after retries) |
| `APIError` | Any other error status; base class of the above |
| `APIConnectionError` | No HTTP answer: DNS, refused connection, timeout |
| `WaitTimeoutError` | `conversations.wait()` ran out of time |
| `WebhookVerificationError` | `webhooks.verify()` rejected a delivery; see `.reason` |

Every `APIError` has `.status`, `.type` (for example `validation_error`), `.message`,
`.fields` (validation messages by dotted path, such as `transcript.0.speaker`) and `.body` (the
parsed JSON or raw text).

```python
from midwater import ValidationError

try:
    client.conversations.create({"external_id": "x", "channel": "fax", "transcript": []})
except ValidationError as exc:
    for path, messages in exc.fields.items():
        print(path, messages)
```

Exception messages and `repr(client)` never include your API key.

## Retries and idempotency

The SDK retries up to `max_retries` times (default 2) on HTTP 429, 500, 502, 503, 504 and on
connection errors, with exponential backoff and jitter (0.5 s, 1 s, 2 s ... capped at 8 s). A
numeric `Retry-After` header is honoured. Other 4xx answers are never retried.

Sending a conversation is retried safely because it always carries an `Idempotency-Key`:
**if you don't pass `idempotency_key`, the SDK generates one (a UUID4) for each `create()` call
and reuses it across that call's retries.** A repeated key answers with the first response and
`accepted.replayed` is `True`. The key used is on `accepted.idempotency_key`.

To make retries safe across processes or restarts too, pass your own key, for example your
`external_id`:

```python
client.conversations.create(payload, idempotency_key=payload["external_id"])
```

Separately, sending an `external_id` that already exists returns the existing conversation
with `accepted.duplicate == True`; nothing new is created.

## Async

```python
import asyncio
from midwater import AsyncMidwater


async def main() -> None:
    async with AsyncMidwater() as client:
        accepted = await client.conversations.create(payload)
        conversation = await client.conversations.wait(accepted.id)
        print(conversation.outcome)


asyncio.run(main())
```

`AsyncMidwater` has the same methods as `Midwater`, as coroutines. Use `async with`, or call
`await client.close()`. The sync client supports `with` and `client.close()`.

## Environments and API keys

Each API key belongs to one environment of one project:

- `mw_test_...`: the **test** environment. Use it in development and CI.
- `mw_live_...`: the **live** environment, for production traffic.
- Keys made before October 2026 start `vk_test_` / `vk_live_` and keep working.

Everything you send and read is scoped to the key's environment: a test key can't see live
conversations, and health is measured separately per environment. The client checks the key's
prefix when it's constructed and raises `AuthenticationError` if it doesn't look like a
Midwater key.

## Configuration

```python
client = Midwater(
    api_key=None,  # default: MIDWATER_API_KEY
    base_url=None,  # default: MIDWATER_BASE_URL, then https://api.midwater.ai
    timeout=30.0,  # seconds per request
    max_retries=2,
    http_client=None,  # your own httpx.Client (AsyncMidwater: httpx.AsyncClient)
)
```

`https://api.midwater.ai` is a placeholder until the hosted API is deployed; it lives in one
constant, `midwater.DEFAULT_BASE_URL`. If you pass `http_client`, its own timeout applies and
the SDK won't close it.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check .
.venv/bin/mypy --strict src
.venv/bin/pytest                 # unit tests
.venv/bin/pytest -m contract     # live tests against a local Midwater stack
.venv/bin/python -m build
```

Contract tests read `MIDWATER_API_KEY` and `MIDWATER_BASE_URL` from the environment or from a
git-ignored `.env.contract` (see `.env.example`). They only run against `localhost` and are
skipped otherwise. The API contract lives in `openapi/midwater.yaml`.

## Releasing

**Nothing is published today.** Publishing needs the owner's go-ahead. When that's given:

1. Create (or confirm) a company-owned `midwater` project on PyPI, owned by a Midwater
   organisation account rather than a personal one, with at least two owners and 2FA.
2. On PyPI, add a *trusted publisher* for the project: this GitHub repository, a workflow such
   as `.github/workflows/release.yml`, and a protected `pypi` environment that requires
   approval. No API tokens are stored anywhere.
3. Add that release workflow: on a published GitHub release (or a `v*` tag), build with
   `python -m build`, then upload with `pypa/gh-action-pypi-publish` using
   `permissions: id-token: write` in the `pypi` environment. Try it against TestPyPI first.
4. Point `DEFAULT_BASE_URL` at the deployed API, bump `src/midwater/_version.py`, move the
   `CHANGELOG.md` entry out of "Unreleased", and tag the release.
5. Remove the "not published yet" note from this README.

The CI workflow in this repository only lints, type-checks, tests and builds; it never
publishes.

## License

MIT. See [LICENSE](LICENSE).
