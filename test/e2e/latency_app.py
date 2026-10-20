"""Serve an e2e app with each htmx request held first (#834).

A dev machine answers a fragment in a few milliseconds and CI in hundreds, so a
test that acts before the previous response has landed passes locally every
time and fails on CI now and then. `E2E_LATENCY_MS` gives every e2e server CI's
timing on demand; `_run_demo_server` serves the app it was asked for, named in
`E2E_APP`, through this one.

Only htmx's requests wait: the races this exposes are between fragment swaps,
and page loads are not part of them. The wait comes before the app sees the
request, so it stands for a slow runner rather than for work the server is doing.
"""

import asyncio
import importlib
import os
from datetime import timedelta

from starlette.types import Receive, Scope, Send

_module, _, _name = os.environ["E2E_APP"].partition(":")
_app = getattr(importlib.import_module(_module), _name)
_wait = timedelta(milliseconds=int(os.environ["E2E_LATENCY_MS"]))


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    if scope["type"] == "http" and any(name == b"hx-request" for name, _ in scope["headers"]):
        await asyncio.sleep(_wait.total_seconds())
    await _app(scope, receive, send)
