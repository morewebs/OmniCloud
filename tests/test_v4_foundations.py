"""Schema v4 + the foundations the IP/billing/purchase work stands on:
migration from v3, multi-field credentials, scoped API tokens, the
purchases toggle, and the no-retry purchase path in the HTTP client."""
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from server import accounts, config, db, secrets
from server.adapters import http as phttp
from server.adapters.base import (AdapterError, Capability, CredentialField,
                                  ProviderAdapter)
from server.adapters.hetzner import HetznerAdapter
from server.main import create_app

from conftest import FakeAdapter

HDRS = {"X-Requested-With": "XMLHttpRequest"}

# the v3 shape of every table v4 touches (verbatim from schema v3)
V3_DDL = """
CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','viewer')),
    disabled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE accounts (id INTEGER PRIMARY KEY, adapter TEXT NOT NULL, name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE actions (id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    provider_id TEXT NOT NULL, kind TEXT NOT NULL,
    requested_by INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL CHECK(status IN ('in_progress','done','failed')),
    detail TEXT, created_at TEXT NOT NULL, completed_at TEXT);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE orders (id INTEGER PRIMARY KEY,
    mode TEXT NOT NULL CHECK(mode IN ('prototype','real')),
    status TEXT NOT NULL CHECK(status IN
        ('draft','confirmed','executing','provisioned','failed','cancelled')),
    adapter TEXT NOT NULL, account_id INTEGER REFERENCES accounts(id),
    plan_name TEXT NOT NULL, location TEXT NOT NULL, options TEXT NOT NULL,
    plan_snapshot TEXT NOT NULL, estimated_monthly TEXT NOT NULL,
    resulting_provider_id TEXT, requested_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE order_events (id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    status TEXT NOT NULL, detail TEXT, created_at TEXT NOT NULL);
CREATE TABLE api_tokens (id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL, last_used_at TEXT);
INSERT INTO settings VALUES ('schema_version', '3');
INSERT INTO users VALUES (1, 'admin', 'x', 'admin', 0, '2026-10-01');
INSERT INTO accounts VALUES (1, 'hetzner', 'account-a7f3', 1, '2026-10-01');
INSERT INTO orders VALUES (1, 'prototype', 'provisioned', 'hetzner', 1, 'cx22', 'fsn1',
    '{}', '{}', '{}', 'proto-1', 1, '2026-10-01', '2026-10-01');
INSERT INTO order_events VALUES (1, 1, 'draft', NULL, '2026-10-01');
INSERT INTO order_events VALUES (2, 1, 'provisioned', NULL, '2026-10-01');
INSERT INTO api_tokens VALUES (1, 1, 'ci', 'hash-a', '2026-10-01', NULL);
"""


def test_v3_database_migrates_to_v4_keeping_order_history(tmp_path, monkeypatch):
    path = tmp_path / "v3.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(V3_DDL)
    monkeypatch.setattr(config, "DB_PATH", path)
    db.init()
    with db.connect() as conn:
        assert conn.execute("SELECT value FROM settings WHERE key='schema_version'"
                            ).fetchone()[0] == "4"
        # the rebuild kept the order AND its events (FK cascade must not fire)
        o = conn.execute("SELECT * FROM orders WHERE id=1").fetchone()
        assert o["status"] == "provisioned" and o["kind"] == "server"
        assert conn.execute("SELECT COUNT(*) FROM order_events").fetchone()[0] == 2
        # the new status is now legal
        conn.execute("UPDATE orders SET status='awaiting_payment' WHERE id=1")
        a = conn.execute("SELECT purchases_enabled FROM accounts WHERE id=1").fetchone()
        assert a[0] == 0  # purchases stay off for existing accounts
        assert conn.execute("SELECT scope FROM api_tokens WHERE id=1").fetchone()[0] == "full"
        assert "result" in {r[1] for r in conn.execute("PRAGMA table_info(actions)")}
        conn.execute("SELECT * FROM billing_snapshots").fetchall()
    db.init()  # idempotent: a second boot is a no-op


def test_fresh_database_is_v4():
    with db.connect() as conn:
        assert conn.execute("SELECT value FROM settings WHERE key='schema_version'"
                            ).fetchone()[0] == "4"


# -- credentials ---------------------------------------------------------------

class PanelAdapter(FakeAdapter):
    key = "panel"
    credential_fields = (
        CredentialField(name="url", label="Panel URL", secret=False,
                        default="https://panel.example.test/billmgr"),
        CredentialField(name="username", label="Username", secret=False),
        CredentialField(name="password", label="Password"),
    )


def test_single_token_credential_is_stored_unchanged(monkeypatch):
    monkeypatch.setitem(accounts.ADAPTERS, "fake", FakeAdapter)
    secret, last4 = accounts.pack_credential("fake", "tok-0000abcd", None)
    assert (secret, last4) == ("tok-0000abcd", "abcd")
    with pytest.raises(ValueError):
        accounts.pack_credential("fake", "  ", None)


def test_multi_field_credential_packs_json_and_never_shows_the_password(monkeypatch):
    monkeypatch.setitem(accounts.ADAPTERS, "panel", PanelAdapter)
    secret, last4 = accounts.pack_credential(
        "panel", None, {"username": " ops@example.test ", "password": "pa ss "})
    data = json.loads(secret)
    assert data == {"url": "https://panel.example.test/billmgr",  # default applied
                    "username": "ops@example.test",            # identifiers trimmed
                    "password": "pa ss "}                       # secrets byte-exact
    assert last4 == "test"  # from the username, never the password
    with pytest.raises(ValueError, match="Password is required"):
        accounts.pack_credential("panel", None, {"username": "ops"})


def test_adapter_parses_its_multi_field_credential():
    class A(ProviderAdapter):
        async def list_servers(self): return []
        async def get_server(self, pid): return None
        async def perform_action(self, cap, sid, params): return None
    a = A(1, "acct", json.dumps({"username": "u", "password": "p"}))
    assert a.credential() == {"username": "u", "password": "p"}
    with pytest.raises(AdapterError):
        A(1, "acct", "not-json").credential()


def test_hetzner_never_advertises_unimplemented_capabilities():
    # an explicit set, not frozenset(Capability): a new enum member (here
    # set_password, which Hetzner doesn't implement) is never auto-advertised
    assert Capability.SET_PASSWORD not in HetznerAdapter.capabilities
    assert HetznerAdapter.capabilities != frozenset(Capability)


# -- HTTP: purchases are sent exactly once --------------------------------------

async def test_no_retry_request_sends_a_purchase_exactly_once():
    calls = []

    def handler(req):
        calls.append(req.method)
        return httpx.Response(503, json={"error": "busy"})
    c = phttp.ProviderHttpClient("https://api.example.test", lambda r: None,
                                 transport=httpx.MockTransport(handler))
    r = await c.request("POST", "/ips", retry=False, json={})
    assert r.status_code == 503 and calls == ["POST"]
    # the default (reads) still retries 5xx
    calls.clear()
    phttp.BACKOFF_LADDER, saved = (0.0, 0.0, 0.0), phttp.BACKOFF_LADDER
    try:
        r = await c.request("GET", "/ips")
    finally:
        phttp.BACKOFF_LADDER = saved
    assert len(calls) == 3 and r.status_code == 503


async def test_dropped_connection_on_a_purchase_says_outcome_unknown():
    def handler(req):
        raise httpx.ConnectError("reset")
    c = phttp.ProviderHttpClient("https://api.example.test", lambda r: None,
                                 transport=httpx.MockTransport(handler))
    with pytest.raises(AdapterError, match="may still have accepted"):
        await c.request("POST", "/ips", retry=False, json={})


# -- API: credential fields, purchases toggle, scoped tokens ---------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(accounts.ADAPTERS, "fake", FakeAdapter)
    monkeypatch.setitem(accounts.ADAPTERS, "panel", PanelAdapter)
    with TestClient(create_app()) as c:
        c.post("/api/auth/setup", json={"username": "admin", "password": "pw123456"},
               headers=HDRS)
        yield c


def test_adapters_publish_their_credential_form(client):
    rows = {a["key"]: a for a in client.get("/api/adapters").json()}
    assert [f["name"] for f in rows["panel"]["credential_fields"]] == \
        ["url", "username", "password"]
    assert rows["fake"]["credential_fields"][0]["name"] == "token"


def test_multi_field_account_created_via_api(client):
    r = client.post("/api/accounts", headers=HDRS, json={
        "adapter": "panel", "name": "panel-a7f3",
        "fields": {"username": "ops@example.test", "password": "s3cret-pw"}})
    assert r.status_code == 200, r.text
    row = next(a for a in client.get("/api/accounts").json() if a["adapter"] == "panel")
    assert row["last4"] == "test" and row["purchases_enabled"] == 0
    with db.connect() as conn:
        ct = conn.execute("SELECT ciphertext FROM credentials WHERE account_id=?",
                          (row["id"],)).fetchone()[0]
    assert json.loads(secrets.decrypt(ct))["password"] == "s3cret-pw"
    r = client.post("/api/accounts", headers=HDRS, json={
        "adapter": "panel", "name": "x", "fields": {"username": "ops"}})
    assert r.status_code == 400 and "Password" in r.json()["detail"]


def test_purchases_toggle_is_admin_only_and_audited(client):
    aid = client.post("/api/accounts", headers=HDRS, json={
        "adapter": "fake", "name": "account-a7f3", "token": "tok-0000abcd"}).json()["id"]
    assert client.patch(f"/api/accounts/{aid}", headers=HDRS,
                        json={"purchases_enabled": True}).status_code == 200
    assert accounts.get_account(aid)["purchases_enabled"] == 1
    actions = [e["action"] for e in client.get("/api/audit").json()]
    assert "account.purchases_enable" in actions


def test_ip_change_token_reaches_only_the_ip_change_api(client):
    tok = client.post("/api/auth/tokens", headers=HDRS,
                      json={"name": "rotator", "scope": "ip_change"}).json()
    assert tok["scope"] == "ip_change"
    b = {"Authorization": f"Bearer {tok['token']}"}
    client.cookies.clear()  # the remote script has no session - only the token
    assert client.get("/api/auth/me", headers=b).status_code == 200
    for path in ("/api/fleet", "/api/accounts", "/api/audit", "/api/billing/summary"):
        assert client.get(path, headers=b).status_code == 403, path
    assert client.post("/api/auth/tokens", headers=b,
                       json={"name": "escalate"}).status_code == 403
    assert client.post("/api/accounts", headers=b, json={
        "adapter": "fake", "name": "x", "token": "t"}).status_code == 403
    client.post("/api/auth/login", headers=HDRS,
                json={"username": "admin", "password": "pw123456"})
    listed = client.get("/api/auth/tokens").json()
    assert listed[0]["scope"] == "ip_change"
    assert client.post("/api/auth/tokens", headers=HDRS,
                       json={"name": "bad", "scope": "root"}).status_code == 400
