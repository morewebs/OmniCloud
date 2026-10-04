"""Updater tests: version truth, check against a mocked GitHub API, apply
pre-flight guards, route permissions. No network."""
import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import FakeAdapter


def test_version_source_of_truth():
    """server/version.py, pyproject.toml and web/package.json must never drift."""
    from server import version
    text = open("pyproject.toml", encoding="utf-8").read()
    assert f'version = "{version.VERSION}"' in text
    pkg = open("web/package.json", encoding="utf-8").read()
    assert f'"version": "{version.VERSION}"' in pkg


def test_is_newer_semver():
    from server.update import is_newer
    assert is_newer("0.3.0", "0.2.0")
    assert is_newer("1.0.0", "0.9.9")
    assert not is_newer("0.2.0", "0.2.0")
    assert not is_newer("0.1.0", "0.2.0")
    assert not is_newer("garbage", "0.2.0")


async def test_check_parses_github_release():
    from server import update
    class FakeClient:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, headers=None):
            r = httpx.Response(200, json={
                "tag_name": "v0.3.0", "html_url": "https://example/r",
                "body": "release notes"})
            r.request = httpx.Request("GET", url)
            return r
    orig = httpx.AsyncClient
    httpx.AsyncClient = FakeClient
    try:
        st = await update.check()
    finally:
        httpx.AsyncClient = orig
    assert st["latest"] == "0.3.0"
    assert st["available"] is True  # 0.3.0 > current
    assert st["url"] == "https://example/r"
    assert st["error"] is None
    assert update.status()["latest"] == "0.3.0"


async def test_check_failure_is_silent():
    from server import update
    update._status.update(latest="9.9.9")  # stale from a previous check
    class DeadClient:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, headers=None):
            raise httpx.ConnectError("no network")
    orig = httpx.AsyncClient
    httpx.AsyncClient = DeadClient
    try:
        st = await update.check()
    finally:
        httpx.AsyncClient = orig
    assert st["latest"] is None
    assert "no network" in st["error"]


async def test_apply_preflight_guards():
    from server import update
    update._status.update(available=True, latest="9.9.9", applying=False)
    try:
        # not a git checkout (a temp dir is not one)
        root = update.REPO_ROOT
        update.REPO_ROOT = root.parent / "nonexistent"
        try:
            with pytest.raises(RuntimeError, match="not a git checkout"):
                await update.apply()
        finally:
            update.REPO_ROOT = root
    finally:
        update._status.update(available=False, latest=None, applying=False)


@pytest.fixture
def client(monkeypatch):
    from server import accounts as accounts_mod, api as api_mod
    from server.main import create_app
    monkeypatch.setitem(accounts_mod.ADAPTERS, "fake", FakeAdapter)
    api_mod._login_fails.clear()
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_update_routes_permissions(client):
    """status/check: any signed-in user. apply: admin only."""
    HDRS = {"X-Requested-With": "XMLHttpRequest"}
    r = client.post("/api/auth/setup", json={"username": "admin", "password": "pw123456"},
                    headers=HDRS)
    assert client.get("/api/update/status", headers=HDRS).status_code == 200
    # viewer: read ok, apply 403
    client.post("/api/users", json={"username": "v", "password": "pw123456",
                                    "role": "viewer"}, headers=HDRS)
    client.post("/api/auth/logout", headers=HDRS)
    client.post("/api/auth/login", json={"username": "v", "password": "pw123456"}, headers=HDRS)
    assert client.get("/api/update/status", headers=HDRS).status_code == 200
    r = client.post("/api/update/apply", headers=HDRS)
    assert r.status_code == 403
    assert client.post("/api/update/check", headers=HDRS).status_code == 200


async def test_apply_refuses_when_no_update_known():
    """A never-checked (or failed-check) panel must not blind-apply a pull."""
    from server import update
    update._status.update(available=False, latest=None, applying=False)
    try:
        with pytest.raises(RuntimeError, match="no newer version"):
            await update.apply()
    finally:
        update._status.update(available=False, latest=None, applying=False)


async def test_apply_missing_git_binary_is_friendly():
    """No git on the box -> the 'update manually' error, not a 500."""
    from server import update
    update._status.update(available=True, latest="9.9.9", applying=False)
    orig = update.subprocess.run

    def no_git(cmd, *a, **kw):
        raise FileNotFoundError("git not found")

    update.subprocess.run = no_git
    try:
        with pytest.raises(RuntimeError, match="git is not installed"):
            await update.apply()
    finally:
        update.subprocess.run = orig
        update._status.update(available=False, latest=None, applying=False)
