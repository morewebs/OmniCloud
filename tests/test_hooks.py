"""Traffic-allowance hooks: threshold evaluation, arming hysteresis,
delivery retry, REST + MCP surfaces, secret hygiene, migration, retention."""
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from server import db, hooks
from server.main import create_app

from conftest import TEST_TOKEN

HDRS = {"X-Requested-With": "XMLHttpRequest"}

SECRET = "Bearer super-secret-xyz"


def _hook_dict(hook_id=1, events=("warn", "over"), warn_pct=80, adapter=None,
               account_id=None, provider_id=None, headers=None, extra=None):
    return {"id": hook_id, "name": f"hook-{hook_id}", "url": "https://example.test/cb",
            "events": list(events), "warn_pct": warn_pct, "adapter": adapter,
            "account_id": account_id, "provider_id": provider_id,
            "headers": headers or {}, "extra": extra or {}, "enabled": True}


def _server_row(pct, account_id=1, provider_id="srv-1", adapter="fake",
                name="srv-a", **allowance_extra):
    used = int(21_990_000_000_000 * pct / 100)
    return {"account_id": account_id, "provider_id": provider_id, "name": name,
            "adapter": adapter, "ipv4": "203.0.113.10",
            "allowance": {"used_bytes": used, "included_bytes": 21_990_000_000_000,
                          "counting": "outgoing_only", **allowance_extra}}


def _mock(status=200, capture=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(request)
        return httpx.Response(status, json={"ok": True})
    return httpx.MockTransport(handler)


def _aclient(status=200, capture=None) -> httpx.AsyncClient:
    """deliver(http=...) expects an AsyncClient; MockTransport alone has no
    .post. The test owns (and closes) it - deliver only closes its own."""
    return httpx.AsyncClient(transport=_mock(status, capture))


# -- evaluation -----------------------------------------------------------------

def test_eval_warn_and_over_levels():
    h = _hook_dict(warn_pct=80)
    assert hooks._server_level(_server_row(79), h) == "none"
    assert hooks._server_level(_server_row(80), h) == "warn"
    assert hooks._server_level(_server_row(100), h) == "over"
    # over disabled: 100% sits at warn
    h2 = _hook_dict(events=("warn",))
    assert hooks._server_level(_server_row(130), h2) == "warn"
    # warn disabled: below 100% is nothing
    h3 = _hook_dict(events=("over",))
    assert hooks._server_level(_server_row(99), h3) == "none"
    assert hooks._server_level(_server_row(100), h3) == "over"


def test_eval_skips_unknown_allowance():
    h = _hook_dict()
    assert hooks._server_level({"allowance": None}, h) is None
    assert hooks._server_level({"allowance": {"used_bytes": None,
                                              "included_bytes": 1}}, h) is None
    # no included number: unknown, never zero
    assert hooks._server_level({"allowance": {"used_bytes": 5,
                                              "included_bytes": None}}, h) is None


def test_eval_filters():
    h = _hook_dict(adapter="hetzner", account_id=7, provider_id="srv-9")
    assert hooks._match(h, {"adapter": "hetzner", "account_id": 7, "provider_id": "srv-9"})
    assert not hooks._match(h, {"adapter": "fake", "account_id": 7, "provider_id": "srv-9"})
    assert not hooks._match(h, {"adapter": "hetzner", "account_id": 8, "provider_id": "srv-9"})
    assert not hooks._match(h, {"adapter": "hetzner", "account_id": 7, "provider_id": "other"})
    empty = _hook_dict()
    assert hooks._match(empty, {"adapter": "anything", "account_id": 99,
                                "provider_id": "x"})  # null filters match all


def test_payload_shape_and_extra_merge():
    h = _hook_dict(headers={"Authorization": SECRET},
                   extra={"channel": "ops"})
    p = hooks.build_payload(h, "warn", _server_row(85, name="srv-a"))
    assert p["event"] == "allowance_warn"
    assert p["channel"] == "ops"                  # extra merged in
    assert p["server"]["name"] == "srv-a"
    assert p["allowance"]["pct"] == 85
    assert p["hook"]["name"] == "hook-1"
    assert "fired_at" in p
    # extra's keys win over built-ins (documented): an operator may override
    # e.g. `event` for their receiver's vocabulary
    h2 = _hook_dict(extra={"event": "custom"})
    assert hooks.build_payload(h2, "over", _server_row(120))["event"] == "custom"
    # the header secret never leaks into the payload body
    assert SECRET not in json.dumps(p)


# -- arming hysteresis -----------------------------------------------------------

async def test_fires_once_per_crossing_and_rearms(monkeypatch):
    monkeypatch.setattr(hooks, "RETRY_DELAYS", ())
    h = _hook_dict()
    db_hook_id = hooks.create_hook("h", h["url"], h["events"], h["warn_pct"],
                                   None, None, None, None, None)
    hook = hooks.get_hook(db_hook_id)
    seen = []
    orig = hooks._spawn

    def spawn(hook, event, server):
        seen.append(event)
        # don't run real deliveries; drive arming like a delivered event would
        hooks._set_armed(hook["id"], server["account_id"], server["provider_id"], event)
    monkeypatch.setattr(hooks, "_spawn", spawn)
    assert orig  # keep lint quiet about the unused original

    row = _server_row(85)
    await hooks.after_sync([row])      # crossing: warn fires
    assert seen == ["warn"]
    await hooks.after_sync([row])      # flat at same level: no re-fire
    assert seen == ["warn"]
    await hooks.after_sync([_server_row(95)])   # still warn: nothing
    assert seen == ["warn"]
    await hooks.after_sync([_server_row(120)])  # upward crossing to over
    assert seen == ["warn", "over"]
    await hooks.after_sync([_server_row(70)])   # fall below: re-arm, no fire
    assert seen == ["warn", "over"]
    await hooks.after_sync([_server_row(85)])   # rises again: fires again
    assert seen == ["warn", "over", "warn"]


async def test_disabled_hook_never_fires(monkeypatch):
    h = _hook_dict()
    db_hook_id = hooks.create_hook("off", h["url"], h["events"], h["warn_pct"],
                                   None, None, None, None, None)
    hooks.update_hook(db_hook_id, {"enabled": False})
    fired = []
    monkeypatch.setattr(hooks, "_spawn",
                        lambda *a: fired.append(a))
    await hooks.after_sync([_server_row(95)])
    assert fired == []


# -- delivery ----------------------------------------------------------------------

async def test_delivery_success_and_arming(monkeypatch):
    monkeypatch.setattr(hooks, "RETRY_DELAYS", ())
    capture = []
    db_hook_id = hooks.create_hook("d", "https://cb.example.test/x",
                                   ["warn", "over"], 80, None, None, None,
                                   {"Authorization": SECRET}, {"team": "ops"})
    hook = hooks.get_hook(db_hook_id)
    client = _aclient(200, capture)
    try:
        result = await hooks.deliver(hook, "warn", _server_row(85, account_id=0),
                                    http=client)
    finally:
        await client.aclose()
    assert result["status"] == "delivered" and result["response_status"] == 200
    # header sent, extra merged, event name mapped
    req = capture[0]
    assert req.headers["Authorization"] == SECRET
    body = json.loads(req.content)
    assert body["event"] == "allowance_warn" and body["team"] == "ops"
    # delivered warn arms the level
    with db.connect() as conn:
        level = conn.execute("SELECT level FROM hook_armed WHERE hook_id=?",
                             (db_hook_id,)).fetchone()[0]
    assert level == "warn"
    # delivery row recorded
    rows = hooks.list_deliveries()
    assert len(rows) == 1 and rows[0]["status"] == "delivered"


async def test_delivery_retries_5xx_then_fails(monkeypatch):
    monkeypatch.setattr(hooks, "RETRY_DELAYS", (0, 0))  # 3 attempts, no sleeping
    db_hook_id = hooks.create_hook("r", "https://cb.example.test/x", ["over"], 80,
                                   None, None, None, None, None)
    hook = hooks.get_hook(db_hook_id)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await hooks.deliver(hook, "over", _server_row(120, account_id=0),
                                     http=client)
    finally:
        await client.aclose()
    assert result["status"] == "failed" and result["response_status"] == 500
    assert len(calls) == 3                    # 1 + 2 retries
    # a failed delivery does NOT arm: the next sync retries the crossing
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM hook_armed WHERE hook_id=?",
                         (db_hook_id,)).fetchone()[0]
    assert n == 0
    assert hooks.list_deliveries()[0]["status"] == "failed"


async def test_delivery_no_retry_on_4xx(monkeypatch):
    monkeypatch.setattr(hooks, "RETRY_DELAYS", ())
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(404)
    db_hook_id = hooks.create_hook("x4", "https://cb.example.test/x", ["warn"], 80,
                                   None, None, None, None, None)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await hooks.deliver(hooks.get_hook(db_hook_id), "warn",
                                     _server_row(85, account_id=0), http=client)
    finally:
        await client.aclose()
    assert result["status"] == "failed" and len(calls) == 1


async def test_send_test_uses_matching_cached_server(monkeypatch):
    monkeypatch.setattr(hooks, "RETRY_DELAYS", ())
    capture = []
    # a cached server the hook's filter matches (servers.account_id has an FK
    # to accounts - create the account first; register the fake adapter)
    from server import accounts as accounts_mod
    from conftest import FakeAdapter
    monkeypatch.setitem(accounts_mod.ADAPTERS, "fake", FakeAdapter)
    accounts_mod.create_account("fake", "acct", TEST_TOKEN, None, None)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO servers(account_id, provider_id, canonical, first_seen_at, last_seen_at)"
            " VALUES(1,'srv-1',?,?,?)",
            (json.dumps(_server_row(50)), db.now(), db.now()))
    db_hook_id = hooks.create_hook("t", "https://cb.example.test/x", ["warn"], 80,
                                   "fake", None, None, None, None)
    client = _aclient(200, capture)
    try:
        result = await hooks.send_test(hooks.get_hook(db_hook_id), http=client)
    finally:
        await client.aclose()
    assert result["status"] == "delivered"
    body = json.loads(capture[0].content)
    assert body["event"] == "test"
    assert body["server"]["provider_id"] == "srv-1"
    # test never arms
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM hook_armed").fetchone()[0]
    assert n == 0


# -- REST + MCP ----------------------------------------------------------------

@pytest.fixture
def app(monkeypatch):
    with TestClient(create_app()) as c:
        c.post("/api/auth/setup", json={"username": "admin", "password": "pw123456"},
               headers=HDRS)
        c.post("/api/users", json={"username": "view", "password": "pw123456",
                                   "role": "viewer"}, headers=HDRS)
        c.post("/api/auth/logout", headers=HDRS)
        c.post("/api/auth/login", json={"username": "view", "password": "pw123456"},
               headers=HDRS)
        viewer_token = c.post("/api/auth/tokens", json={"name": "v"},
                              headers=HDRS).json()["token"]
        c.cookies.clear()
        # the client leaves the fixture signed in as admin - the default for
        # these tests; the 401 case clears cookies itself
        c.post("/api/auth/login", json={"username": "admin", "password": "pw123456"},
               headers=HDRS)
        yield c, viewer_token


def _rpc(c, token, tool, /, **args):
    r = c.post("/mcp", headers={
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
        "Authorization": f"Bearer {token}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": tool, "arguments": args}})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    # an empty JSON result (e.g. []) serializes to no content blocks at all
    text = res["content"][0]["text"] if res.get("content") else "[]"
    return res["isError"], (text if res["isError"] else json.loads(text))


def test_rest_crud_and_secret_hygiene(app):
    c, viewer_token = app
    # not signed in: the hooks surface is admin-only either way
    c.cookies.clear()
    assert c.get("/api/hooks", headers=HDRS).status_code == 401
    # sign back in as admin (the fixture re-login happens at teardown; do it now)
    c.post("/api/auth/login", json={"username": "admin", "password": "pw123456"},
           headers=HDRS)
    # create
    r = c.post("/api/hooks", json={"name": "ops", "url": "https://cb.example.test/x",
                                   "headers": {"Authorization": SECRET},
                                   "extra": {"team": "ops"}}, headers=HDRS)
    assert r.status_code == 200, r.text
    hook_id = r.json()["id"]
    # listing: header NAMES only, never the value; url present (it's the config)
    listed = c.get("/api/hooks", headers=HDRS).json()
    assert listed[0]["header_names"] == ["Authorization"]
    assert SECRET not in json.dumps(listed)
    # validation: bad URL, bad events, unknown account -> 400; warn_pct out
    # of range is rejected by the body model itself (422)
    assert c.post("/api/hooks", json={"name": "x", "url": "ftp://nope"},
                  headers=HDRS).status_code == 400
    assert c.post("/api/hooks", json={"name": "x", "url": "https://a.test/x",
                                      "events": ["bogus"]}, headers=HDRS).status_code == 400
    assert c.post("/api/hooks", json={"name": "x", "url": "https://a.test/x",
                                      "warn_pct": 0}, headers=HDRS).status_code == 422
    assert c.post("/api/hooks", json={"name": "x", "url": "https://a.test/x",
                                      "account_id": 99}, headers=HDRS).status_code == 400
    # patch: partial update keeps the rest
    r = c.patch(f"/api/hooks/{hook_id}", json={"warn_pct": 90,
                                               "headers": {}}, headers=HDRS)
    assert r.status_code == 200
    listed = c.get("/api/hooks", headers=HDRS).json()[0]
    assert listed["warn_pct"] == 90 and listed["header_names"] == []
    # 404s
    assert c.patch("/api/hooks/99", json={"warn_pct": 90},
                   headers=HDRS).status_code == 404
    assert c.delete("/api/hooks/99", headers=HDRS).status_code == 404
    # audit trail
    audit = c.get("/api/audit", headers=HDRS).json()
    assert any(a["action"] == "hook.create" for a in audit)
    assert any(a["action"] == "hook.update" for a in audit)
    # delete
    assert c.delete(f"/api/hooks/{hook_id}", headers=HDRS).status_code == 200
    assert c.get("/api/hooks", headers=HDRS).json() == []


def test_rest_test_endpoint_and_deliveries(app, monkeypatch):
    c, _ = app
    monkeypatch.setattr(hooks, "RETRY_DELAYS", ())
    hook_id = c.post("/api/hooks", json={"name": "ops", "url": "https://cb.example.test/x"},
                     headers=HDRS).json()["id"]
    # no cached servers -> synthetic sample server, MockTransport not needed:
    # the route's own POST goes to cb.example.test - intercept it
    orig = hooks.deliver

    async def fake_deliver(hook, event, server, http=None):
        return {"status": "delivered", "response_status": 200, "error": None,
                "hook_id": hook["id"], "event": "test",
                "account_id": server.get("account_id"),
                "provider_id": server.get("provider_id"),
                "server_name": server.get("name")}
    monkeypatch.setattr(hooks, "deliver", fake_deliver)
    r = c.post(f"/api/hooks/{hook_id}/test", headers=HDRS)
    assert r.status_code == 200 and r.json()["delivery"]["status"] == "delivered"
    # failure maps to 502 with the reason
    async def failing(hook, event, server, http=None):
        return {"status": "failed", "response_status": 500, "error": "HTTP 500",
                "hook_id": hook["id"], "event": "test", "account_id": 1,
                "provider_id": "x", "server_name": None}
    monkeypatch.setattr(hooks, "deliver", failing)
    assert c.post(f"/api/hooks/{hook_id}/test", headers=HDRS).status_code == 502
    monkeypatch.setattr(hooks, "deliver", orig)
    # deliveries log route
    r = c.get("/api/hooks/deliveries", headers=HDRS)
    assert r.status_code == 200 and r.json() == []


def test_mcp_hook_tools(app, monkeypatch):
    c, viewer_token = app
    monkeypatch.setattr(hooks, "RETRY_DELAYS", ())
    real_deliver = hooks.deliver
    admin = c.post("/api/auth/tokens", json={"name": "m"}, headers=HDRS).json()["token"]
    # surface present
    tools = {t["name"] for t in c.post("/mcp", headers={
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
        "Authorization": f"Bearer {admin}"}, json={"jsonrpc": "2.0", "id": 1,
        "method": "tools/list"}).json()["result"]["tools"]}
    assert {"list_hooks", "create_hook", "update_hook", "delete_hook",
            "test_hook", "list_hook_deliveries"} <= tools
    # viewer denied
    err, msg = _rpc(c, viewer_token, "list_hooks")
    assert err and msg.endswith("403: Admin role required")
    # create -> list -> test -> deliveries -> update -> delete
    out = _rpc(c, admin, "create_hook", name="ops",
               url="https://cb.example.test/x", headers={"Authorization": SECRET},
               extra={"team": "ops"})[1]
    hook_id = out["id"]
    listed = _rpc(c, admin, "list_hooks")[1]["hooks"]
    assert listed[0]["header_names"] == ["Authorization"]
    assert SECRET not in json.dumps(listed)
    async def fake_deliver(hook, event, server, http=None):
        return {"status": "delivered", "response_status": 200, "error": None,
                "hook_id": hook["id"], "event": "test",
                "account_id": server.get("account_id"),
                "provider_id": server.get("provider_id"),
                "server_name": server.get("name")}
    monkeypatch.setattr(hooks, "deliver", fake_deliver)
    assert _rpc(c, admin, "test_hook", hook_id=hook_id)[1]["delivery"]["status"] == "delivered"
    # (never monkeypatch.undo(): it would revert the autouse test_env too)
    monkeypatch.setattr(hooks, "deliver", real_deliver)
    _rpc(c, admin, "update_hook", hook_id=hook_id, warn_pct=95)
    assert _rpc(c, admin, "list_hooks")[1]["hooks"][0]["warn_pct"] == 95
    assert _rpc(c, admin, "delete_hook", hook_id=hook_id)[1] == {"ok": True}
    assert _rpc(c, admin, "list_hooks")[1]["hooks"] == []
    # secret hygiene across the whole surface
    for tool, args in (("list_hooks", {}), ("list_hook_deliveries", {})):
        err, out = _rpc(c, admin, tool, **args)
        assert not err, out
        assert SECRET not in json.dumps(out), tool


# -- migration + retention --------------------------------------------------------

def test_v5_to_v6_migration(tmp_path, monkeypatch):
    from server import config
    p = str(tmp_path / "v5.db")
    conn = sqlite3.connect(p)
    conn.executescript("""
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    INSERT INTO settings VALUES ('schema_version', '5');
    """)
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DB_PATH", __import__("pathlib").Path(p))
    db.init()
    with sqlite3.connect(p) as conn:
        assert conn.execute("SELECT value FROM settings WHERE key='schema_version'"
                            ).fetchone()[0] == "6"
        for table in ("traffic_hooks", "hook_deliveries", "hook_armed"):
            assert conn.execute(
                f"SELECT COUNT(*) FROM sqlite_master WHERE name='{table}'"
            ).fetchone()[0] == 1, table


def test_retention_sweeps_old_deliveries(monkeypatch):
    from server import catalog
    hooks.create_hook("s", "https://cb.example.test/x", ["warn"], 80, None, None,
                      None, None, None)
    with db.connect() as conn:
        conn.execute("INSERT INTO hook_deliveries(hook_id, event, account_id, "
                     "provider_id, server_name, status, response_status, error, created_at) "
                     "VALUES(1,'warn',1,'srv','s','delivered',200,NULL,"
                     "datetime('now', '-400 days'))")
        conn.execute("INSERT INTO hook_deliveries(hook_id, event, account_id, "
                     "provider_id, server_name, status, response_status, error, created_at) "
                     "VALUES(1,'over',1,'srv','s','delivered',200,NULL,"
                     "datetime('now'))")
    catalog._sweep()
    assert len(hooks.list_deliveries()) == 1
