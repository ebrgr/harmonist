"""E2E_LATENCY_MS gives the e2e servers CI's response times on demand (#834)."""

import os
import time
import urllib.request
from datetime import timedelta

import pytest
from test.e2e.conftest import _run_demo_server

pytestmark = pytest.mark.skipif(os.environ.get("RUN_E2E") != "1", reason="e2e disabled")

LATENCY = timedelta(milliseconds=500)


def _elapsed(url: str, headers: dict[str, str]) -> timedelta:
    start = time.monotonic()
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30):
        pass
    return timedelta(seconds=time.monotonic() - start)


def test_latency_holds_htmx_requests_only(tmp_path, monkeypatch):
    monkeypatch.setenv("E2E_LATENCY_MS", str(LATENCY // timedelta(milliseconds=1)))
    for base in _run_demo_server(tmp_path):
        assert _elapsed(f"{base}/status", {"HX-Request": "true"}) >= LATENCY
        # Page loads are not part of the races it exists to expose.
        assert _elapsed(f"{base}/status", {}) < LATENCY
