"""Pause real demo writes at each track so browser progress is observable."""

import threading

from fastapi import FastAPI
from test.e2e import demo_app

from harmonist import formats

app: FastAPI = demo_app.app
_kind = ""
_step = threading.Event()
_write_tags = formats.write_tags
_write_cover = formats.write_cover


@app.post("/test/write/arm/{kind}")
def arm(kind: str):
    global _kind
    _kind = kind
    _step.clear()
    return {}


@app.post("/test/write/continue")
def advance():
    _step.set()
    return {}


def pause(kind):
    if _kind == kind:
        if not _step.wait(timeout=10):
            raise RuntimeError("browser did not release the paused test write")
        _step.clear()


def write_tags(*args, **kwargs):
    pause("tags")
    return _write_tags(*args, **kwargs)


def write_cover(*args, **kwargs):
    pause("artwork")
    return _write_cover(*args, **kwargs)


formats.write_tags = write_tags
formats.write_cover = write_cover
