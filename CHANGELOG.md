# Changelog

All notable changes to the `midwater` package are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- Requires Python 3.10 or later (Python 3.9 reached end of life in October 2025). CI tests 3.10 and 3.13. 0.1.0 on PyPI still declares 3.9; the next release (0.1.1) carries the new floor.

## [0.1.0] - 2026-10-08

**Unpublished.** This version is not on PyPI.

### Added

- `Midwater` (sync) and `AsyncMidwater` (async) clients on `httpx`. `api_key` and `base_url`
  are both required (arguments, or `MIDWATER_API_KEY` and `MIDWATER_BASE_URL`); there is no
  default host, and a missing base URL raises `MidwaterError`.
- `conversations.create()`, `get()`, `wait()` and `feedback()`; `agents.health()`;
  `groups.health()`.
- Typed request payloads (`ConversationCreate`, `FeedbackCreate`) and response models
  (`ConversationAccepted`, `Conversation`, `CheckResult`, `Feedback`, `AgentHealth`,
  `GroupHealth`, `HealthWindow`, `WebhookEvent`). Unknown fields and enum values pass through.
- Error classes mapped from HTTP status: `ValidationError` (400, 422), `AuthenticationError`
  (401), `PermissionDeniedError` (403), `NotFoundError` (404), `RequestTimeoutError` (408),
  `IdempotencyConflictError` (409), `PayloadTooLargeError` (413), `RateLimitError` (429),
  `ServiceUnavailableError` (503, a `ServerError`), `ServerError` (other 5xx) and `APIError`
  (anything else). Each has `.status`, `.type`, `.message`, `.fields`, `.body` and
  `.request_id` (from `error.request_id` or the `Midwater-Request-Id` header; both planned
  server-side). Successful responses expose `.request_id` too.
- Retries on 408, 429, 5xx and network errors, only for calls that are safe to repeat (GETs
  and `conversations.create()`), with exponential backoff, jitter and `Retry-After` (up to
  60 s). `max_retries` defaults to 2 and is capped at 3.
- An `Idempotency-Key` on every POST (generated UUID4 unless passed). `feedback()` takes an
  `idempotency_key` but is never retried until the server honours the key on that endpoint.
- `webhooks.verify()` (also `midwater.verify_webhook`) and `webhooks.sign()` for
  `Midwater-Signature`.
- Shared fixtures in `fixtures/`, pinned by `fixtures/SHA256SUMS` and checked in CI and by
  `tests/test_pins.py`.
