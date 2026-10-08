"""Contract tests run against a real local Midwater stack.

They run only with ``pytest -m contract`` and only when credentials are available, from the
environment (``MIDWATER_API_KEY`` + ``MIDWATER_BASE_URL``) or from the git-ignored
``.env.contract`` at the repository root. The base URL must be localhost so these tests can
never write to a shared environment.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Tuple
from urllib.parse import urlparse

import pytest

ENV_FILE = Path(__file__).resolve().parents[2] / ".env.contract"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _read_env_file(path: Path) -> Dict[str, str]:
    """A tiny KEY=VALUE parser: blank lines and # comments skipped, optional quotes removed."""
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


@pytest.fixture(scope="session")
def contract_env() -> Tuple[str, str]:
    """(base_url, api_key) for the local stack, or skip."""
    file_values = _read_env_file(ENV_FILE)
    base_url = os.environ.get("MIDWATER_BASE_URL") or file_values.get("MIDWATER_BASE_URL")
    api_key = os.environ.get("MIDWATER_API_KEY") or file_values.get("MIDWATER_API_KEY")
    if not base_url or not api_key:
        pytest.skip("No contract credentials (.env.contract or MIDWATER_API_KEY/BASE_URL)")
    host = urlparse(base_url).hostname or ""
    if host not in LOCAL_HOSTS:
        pytest.skip("Contract tests only run against a localhost Midwater stack")
    return base_url, api_key


@pytest.fixture(autouse=True)
def _clean_env() -> None:
    """Overrides the unit-test fixture that clears MIDWATER_* variables."""
