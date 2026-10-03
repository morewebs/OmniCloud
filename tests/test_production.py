"""Production-hardness tests: thread-safety, no-network guarantee, races,
auth hardening. Each maps to a defect found in the production-readiness
audit."""
import asyncio
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient


async def test_publish_from_thread_is_threadsafe():
    """publish() called from a threadpool thread (sync def routes) must hop
    onto the app loop instead of mutating queue waiters off-loop."""
    from server import sync

    # this test runs on the loop pytest-asyncio provides; register it as the app loop
    sync._loop = asyncio.get_running_loop()
    q = await sync.subscribe()
    try:
        # call publish() from a plain thread with NO running loop
        t = threading.Thread(target=lambda: sync.publish("order", {"id": 1}))
        t.start(); t.join()
        # give the call_soon_threadsafe hop a beat to run
        await asyncio.sleep(0.05)
        payload = q.get_nowait()
        assert '"order"' in payload
    finally:
        sync.unsubscribe(q)
        sync._loop = None


def test_testclient_never_touches_the_network(monkeypatch):
    """Guard: with outbound HTTP counted AND poisoned, the whole app (startup
    included) must not make a single real request. Counting matters: the
    catalog loop swallows Exception subclasses, so a raise-only poison can
    pass silently - the counter cannot lie."""
    from server.main import create_app

    calls = []

    def poison(self, request, **kw):
        calls.append(str(request.url))
        raise AssertionError(f"outbound HTTP during test: {request.url}")

    monkeypatch.setattr(httpx.AsyncClient, "send", poison)
    monkeypatch.setattr(httpx.Client, "send", poison)

    app = create_app()
    with TestClient(app) as c:
        # startup ran catalog.start() and sync.start_all()
        assert c.get("/api/auth/status").status_code == 200
        c.post("/api/auth/setup", json={"username": "admin", "password": "pw123456"},
               headers={"X-Requested-With": "XMLHttpRequest"})
        c.post("/api/auth/login", json={"username": "admin", "password": "pw123456"},
               headers={"X-Requested-With": "XMLHttpRequest"})
        assert c.get("/api/fleet", headers={"X-Requested-With": "XMLHttpRequest"}).status_code == 200
        # give the catalog loop's startup pass a beat to (not) fire
        time.sleep(0.5)
    assert calls == [], f"test made outbound HTTP: {calls[:3]}"
