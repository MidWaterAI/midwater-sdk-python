"""Contract tests run against a real local Midwater stack.

They run only with ``pytest -m contract`` and only when ``MIDWATER_API_KEY`` and
``MIDWATER_BASE_URL`` are set in the environment; otherwise they are skipped. The base URL must
be localhost so these tests can never write to a shared environment.
"""

from __future__ import annotations

import os
from typing import Tuple
from urllib.parse import urlparse

import pytest

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@pytest.fixture(scope="session")
def contract_env() -> Tuple[str, str]:
    """(base_url, api_key) for the local stack, from the environment only, or skip."""
    base_url = os.environ.get("MIDWATER_BASE_URL")
    api_key = os.environ.get("MIDWATER_API_KEY")
    if not base_url or not api_key:
        pytest.skip("Set MIDWATER_API_KEY and MIDWATER_BASE_URL to run the contract tests")
    host = urlparse(base_url).hostname or ""
    if host not in LOCAL_HOSTS:
        pytest.skip("Contract tests only run against a localhost Midwater stack")
    return base_url, api_key


@pytest.fixture(autouse=True)
def _clean_env() -> None:
    """Overrides the unit-test fixture that clears MIDWATER_* variables."""
