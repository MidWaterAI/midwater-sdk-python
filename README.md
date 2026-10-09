# Midwater Python SDK

The official Python client for the [Midwater](https://midwater.ai) API. Midwater checks every
conversation your AI agents have, voice calls and chats. Send each finished conversation with
its transcript; Midwater's model scores it in the background, and you read back the outcome,
the result of every check, and each agent's health.

- Sync (`Midwater`) and async (`AsyncMidwater`) clients built on `httpx`
- Typed request payloads and response models (`py.typed`)
- Automatic retries with backoff, only on calls that are safe to repeat
- An `Idempotency-Key` on every POST
- Webhook signature verification
- Python 3.10 and later (3.9 reached end of life in October 2025)

## Install

```bash
pip install midwater
```

> **Version 0.1.0 is unpublished.** The package is not on PyPI yet. Until it is, install
> from a checkout: `pip install -e /path/to/midwater-sdk-python`.

## Quickstart

Set your API key and the base URL for your Midwater environment. Both are required; there is
no default host.

```bash
export MIDWATER_API_KEY=mw_test_...
export MIDWATER_BASE_URL="<the base URL for your Midwater environment>"
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

**Planned renames.** Three values are getting new names: `outcome` `escalated` → `handed_to_person`, `not_real_inquiry` → `not_customer_call`, and `decided_by` `llm_judge` → `second_review`. The SDK's types already accept both old and new names, so handle both until the change is announced; the field name `verdict` stays.

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

Like every POST, feedback carries an `Idempotency-Key` (a generated UUID4, or pass your own
with `idempotency_key=`). The SDK never retries feedback, not even after a `429` or a
connection error: until the server de-duplicates feedback by that key (planned), a retry could
store the same label twice.

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
any match passes. `webhooks.verify()` (also exported as `midwater.verify_webhook`, same
signature) checks the signature and the timestamp (default tolerance 300 seconds) and returns
the parsed event.

**Always pass the exact raw bytes you received.** Don't parse the JSON and serialise it again
first; that changes the bytes and the signature won't match.

Flask:

```python
import os
from flask import Flask, request
from midwater import verify_webhook, WebhookVerificationError

app = Flask(__name__)
SECRET = os.environ["MIDWATER_WEBHOOK_SECRET"]


@app.post("/midwater/webhooks")
def midwater_webhook():
    try:
        event = verify_webhook(request.get_data(), request.headers, SECRET)
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
`stale_timestamp`, `invalid_signature` or `no_secret`. Header lookup is case-insensitive.
Only `Midwater-Signature` is read. Each delivery also has `Midwater-Event` (the event type) and
`Midwater-Delivery` (the same on every retry of one delivery, useful for de-duplication).
Answer with any 2xx within 5 seconds; anything else is retried.

For your own tests, `webhooks.sign(payload, secret, timestamp=None)` builds a valid header value.

## Errors

All errors derive from `midwater.MidwaterError`.

| Exception | When | `.type` |
| --- | --- | --- |
| `ValidationError` | HTTP 400 (invalid JSON) or 422 (schema); see `.fields` | `invalid_json`, `validation_error` |
| `AuthenticationError` | HTTP 401, or no usable API key at construction | `authentication_error` |
| `PermissionDeniedError` | HTTP 403 | `permission_denied` |
| `NotFoundError` | HTTP 404 | `not_found` |
| `MethodNotAllowedError` | HTTP 405 (planned as JSON, with an `Allow` header) | `method_not_allowed` |
| `RequestTimeoutError` | HTTP 408 (after retries) | |
| `IdempotencyConflictError` | HTTP 409: the `Idempotency-Key` was used with a different body | `idempotency_conflict` |
| `PayloadTooLargeError` | HTTP 413 | `payload_too_large` |
| `RateLimitError` | HTTP 429 (after retries) | `rate_limited` |
| `ServiceUnavailableError` | HTTP 503 (after retries); a `ServerError` | `service_unavailable` |
| `ServerError` | Any other HTTP 5xx (after retries) | `server_error` |
| `APIError` | Any other error status; base class of the above | |
| `APIConnectionError` | No HTTP answer: DNS, refused connection, timeout | |
| `WaitTimeoutError` | `conversations.wait()` ran out of time | |
| `WebhookVerificationError` | `webhooks.verify()` rejected a delivery; see `.reason` | |

`MidwaterError` itself is raised when no base URL is configured.

The class is chosen from the HTTP status, never from `.type`, so an error type added to the API
later never breaks your error handling.

Every `APIError` has `.status`, `.type`, `.message`, `.fields` (validation messages by dotted
path, such as `transcript.0.speaker`), `.body` (the parsed JSON or raw text) and `.request_id`.
`.request_id` comes from `error.request_id` in the body, else the `Midwater-Request-Id`
response header, else `None`. Both are **planned** on the server, so expect `None` for now;
quote it to support once it appears. Successful responses carry the header's value on
`.request_id` too (also `None` for now).

```python
from midwater import ValidationError

try:
    client.conversations.create({"external_id": "x", "channel": "fax", "transcript": []})
except ValidationError as exc:
    for path, messages in exc.fields.items():
        print(path, messages)
```

Exception messages, log output and `repr(client)` never include your API key (a test turns on
DEBUG logging for the SDK, `httpx` and `httpcore` and checks).

## Retries and idempotency

The SDK retries HTTP 408, 429, every 5xx, and network errors (connection failures and
timeouts), **only on calls that are safe to repeat**: the GETs (`conversations.get()`,
`wait()`, `agents.health()`, `groups.health()`) and `conversations.create()`, which always
carries an `Idempotency-Key`. `conversations.feedback()` is never retried (see
[Feedback](#feedback)). Other 4xx answers are never retried.

`max_retries` defaults to 2 and is capped at 3: a larger value is treated as 3. Retries use
exponential backoff with jitter (0.5 s, 1 s, 2 s, capped at 8 s). A numeric `Retry-After`
header (seconds) is honoured, up to 60 s.

Every POST carries an `Idempotency-Key`. **If you don't pass `idempotency_key`, the SDK
generates one (a UUID4) for each call and reuses it across that call's retries.** A repeated
key answers with the first response and `accepted.replayed` is `True`. The key used for a
conversation is on `accepted.idempotency_key`. Two parts of this are planned on the server and
not live yet: keys expiring after 24 hours, and answering `409 idempotency_conflict`
(`IdempotencyConflictError`) when a key is reused with a different body.

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

Keys made before October 2026 start `vk_test_` / `vk_live_` and still work.

Everything you send and read is scoped to the key's environment: a test key can't see live
conversations, and health is measured separately per environment. The client checks the key's
prefix when it's constructed and raises `AuthenticationError` if it doesn't look like a
Midwater key.

## Configuration

```python
client = Midwater(
    api_key=None,  # required: pass it or set MIDWATER_API_KEY
    base_url=None,  # required: pass it or set MIDWATER_BASE_URL
    timeout=30.0,  # seconds per request
    max_retries=2,  # at most 3
    http_client=None,  # your own httpx.Client (AsyncMidwater: httpx.AsyncClient)
)
```

`base_url` is the base URL for your Midwater environment. There is no default host: without
`base_url` or `MIDWATER_BASE_URL` the client raises `MidwaterError`. If you pass `http_client`,
its own timeout applies and the SDK won't close it.

## Versioning

Every path is under `/v1`. Within `/v1` the API may add response fields and new values to
enum-like fields (statuses, outcomes, check results, error types). The SDK passes both through
instead of rejecting them, and your code should accept them too: treat an unknown value as
"something new", and read new fields from `.raw` until the SDK names them.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy --strict src
sha256sum -c fixtures/SHA256SUMS  # macOS: shasum -a 256 -c fixtures/SHA256SUMS
.venv/bin/pytest --cov=midwater --cov-report=term-missing --cov-fail-under=90  # unit tests
.venv/bin/pytest -m contract     # live tests against a local Midwater stack
.venv/bin/python -m build
```

Contract tests read `MIDWATER_API_KEY` and `MIDWATER_BASE_URL` from the environment only, and
are skipped when either is unset. They only run against `localhost`. The API contract lives in
`openapi/midwater.yaml`.

### Shared fixtures

`fixtures/` holds JSON shared by every Midwater SDK: a conversation payload, the API's answers
(`conversation.json`, `conversation-accepted.json`, `conversation-duplicate.json`,
`feedback.json`, `agent-health.json`, `group-health.json`), every error type with its status
(`errors.json`) and the webhook signature vectors (`webhook-vectors.json`). The unit tests mock
the API with these files. They are generated in another repository and copied in unchanged;
don't edit them here. `fixtures/SHA256SUMS` pins their checksums and the checksum of
`openapi/midwater.yaml`: CI runs `sha256sum -c fixtures/SHA256SUMS`, and `tests/test_pins.py`
fails if any pinned file drifts.

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
4. Bump `src/midwater/_version.py` if needed, move the `CHANGELOG.md` entry out of
   "Unreleased", and tag the release.
5. Remove the "unpublished" note from this README.

The CI workflow in this repository only lints, type-checks, tests and builds; it never
publishes.

## License

MIT. See [LICENSE](LICENSE).
