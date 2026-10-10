"""The shared fixtures and the OpenAPI document are pinned by ``fixtures/SHA256SUMS``.

Both are generated elsewhere and copied in unchanged. If a copy drifts from its pin, this
test fails: update the files and the checksum file together, from the source repository.
"""

from __future__ import annotations

import hashlib
from typing import List, Tuple

import pytest

from helpers import FIXTURES_DIR, REPO_ROOT

SUMS_FILE = FIXTURES_DIR / "SHA256SUMS"


def _pins() -> List[Tuple[str, str]]:
    pins: List[Tuple[str, str]] = []
    for line in SUMS_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, path = line.partition("  ")
        pins.append((path.strip().lstrip("*"), digest.strip()))
    return pins


PINS = _pins()


def test_pin_file_covers_contract_and_every_fixture() -> None:
    pinned = {path for path, _ in PINS}
    assert "openapi/midwater.json" in pinned
    fixtures = {f"fixtures/{p.name}" for p in FIXTURES_DIR.glob("*.json")}
    assert fixtures and fixtures <= pinned


@pytest.mark.parametrize(("path", "digest"), PINS, ids=[path for path, _ in PINS])
def test_pinned_file_unchanged(path: str, digest: str) -> None:
    actual = hashlib.sha256((REPO_ROOT / path).read_bytes()).hexdigest()
    assert actual == digest, f"{path} drifted from fixtures/SHA256SUMS"


def test_pinned_api_spec_matches_its_checksum() -> None:
    """openapi/midwater.json is a pinned copy of the app's spec, with checksum and source."""
    root = FIXTURES_DIR.parent
    digest, path = (root / "openapi" / "midwater.json.sha256").read_text().split()
    assert path == "openapi/midwater.json"
    assert hashlib.sha256((root / path).read_bytes()).hexdigest() == digest
    assert "openapi/midwater.json" in (root / "openapi" / "SOURCE").read_text()
