# Changelog

All notable changes to the `midwater` package are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - Unreleased

### Added

- `Midwater` (sync) and `AsyncMidwater` (async) clients on `httpx`.
- `conversations.create()`, `get()`, `wait()` and `feedback()`; `agents.health()`;
  `groups.health()`.
- Typed request payloads (`ConversationCreate`, `FeedbackCreate`) and response models
  (`ConversationAccepted`, `Conversation`, `CheckResult`, `Feedback`, `AgentHealth`,
  `GroupHealth`, `HealthWindow`, `WebhookEvent`).
- Error classes mapped from HTTP status, with `.status`, `.type`, `.message`, `.fields`, `.body`.
- Retries with exponential backoff and jitter on 429, 5xx and connection errors; automatic
  `Idempotency-Key` for `conversations.create()`.
- `webhooks.verify()` and `webhooks.sign()` for `Midwater-Signature`.
