"""MCP endpoint (/mcp) over TestClient: Bearer gate, role parity with the
REST routes, action lifecycle, error mapping, secret hygiene."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from server import accounts, catalog, sync
from server.adapters.base import Money, Plan
from server.main import create_app

from conftest import TEST_TOKEN, FakeAdapter

HDRS = {"X-Requested-With": "XMLHttpRequest"}


class SlowFakeAdapter(FakeAdapter):
    async def perform_action(self, cap, server_id, params):
        import asyncio
        await asyncio.sleep(0.5)
        return await super().perform_action(cap, server_id, params)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(accounts.ADAPTERS, "fake", FakeAdapter)
    monkeypatch.setitem(accounts.ADAPTERS, "slowfake", SlowFakeAdapter)
    with TestClient(create_app()) as c:
        yield c


def _mint(c) -> str:
    return c.post("/api/auth/tokens", json={"name": "mcp"}, headers=HDRS).json()["token"]


@pytest.fixture
def tokens(client):
    """(admin token, viewer token). The cookie jar is cleared afterwards so
    /mcp only ever sees the Bearer header."""
    client.post("/api/auth/setup", json={"username": "admin", "password": "pw123456"},
                headers=HDRS)
    admin = _mint(client)
    client.post("/api/users", json={"username": "view", "password": "pw123456",
                                    "role": "viewer"}, headers=HDRS)
    client.post("/api/auth/logout", headers=HDRS)
    client.post("/api/auth/login", json={"username": "view", "password": "pw123456"},
                headers=HDRS)
    viewer = _mint(client)
    client.cookies.clear()
    return admin, viewer


def rpc(c, token, method, params=None):
    headers = {"Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": "2025-06-18"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return c.post("/mcp", headers=headers,
                  json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


def call(c, token, tool, /, **args):
    """-> (is_error, parsed result or error text)."""
    r = rpc(c, token, "tools/call", {"name": tool, "arguments": args})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    text = res["content"][0]["text"]
    return res["isError"], (text if res["isError"] else json.loads(text))


def ok(c, token, tool, /, **args):
    err, out = call(c, token, tool, **args)
    assert not err, out
    return out


async def _fake_account(c, admin, adapter="fake") -> int:
    account_id = ok(c, admin, "create_account", adapter=adapter, name="account-a7f3",
                    token=TEST_TOKEN)["id"]
    await sync.sync_account_now(account_id)
    return account_id


def test_requires_valid_bearer_token(client, tokens):
    r = rpc(client, None, "tools/list")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert rpc(client, "not-a-real-token", "tools/list").status_code == 401
    # a signed-in browser session is NOT an MCP credential (no CSRF surface)
    client.post("/api/auth/login", json={"username": "admin", "password": "pw123456"},
                headers=HDRS)
    assert client.get("/api/auth/me").status_code == 200
    assert rpc(client, None, "tools/list").status_code == 401


def test_initialize_and_tool_surface(client, tokens):
    admin, _ = tokens
    init = rpc(client, admin, "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"}}).json()["result"]
    assert "never zero" in init["instructions"]
    tools = {t["name"]: t for t in rpc(client, admin, "tools/list").json()["result"]["tools"]}
    for name in ("get_overview", "list_fleet", "get_server", "power_server",
                 "create_account", "create_order", "update_settings", "apply_update"):
        assert name in tools
    destructive = {n for n, t in tools.items()
                   if (t.get("annotations") or {}).get("destructiveHint")}
    assert {"rebuild_server", "delete_server", "delete_account", "apply_update"} <= destructive
    assert (tools["get_overview"]["annotations"] or {}).get("readOnlyHint") is True
    prompts = {p["name"] for p in rpc(client, admin, "prompts/list").json()["result"]["prompts"]}
    assert prompts == {"fleet_triage", "cost_review", "compare_plans"}


async def test_viewer_reads_but_cannot_mutate(client, tokens):
    admin, viewer = tokens
    account_id = await _fake_account(client, admin)
    assert ok(client, viewer, "whoami")["role"] == "viewer"
    assert ok(client, viewer, "get_overview")["fleet"]["total"] == 1
    assert ok(client, viewer, "get_server", account_id=account_id,
              provider_id="fake-1")["name"] == "srv-fake-01"
    for name, args in (("rename_server", {"account_id": account_id,
                                          "provider_id": "fake-1", "name": "x"}),
                       ("create_account", {"adapter": "fake", "name": "n", "token": "t"}),
                       ("delete_account", {"account_id": account_id}),
                       ("list_audit_log", {}),
                       ("update_settings", {"settings": {"sync_default_interval": "1"}})):
        err, msg = call(client, viewer, name, **args)
        assert err and msg.endswith("403: Admin role required"), (name, msg)
    # refused before any action row was written
    assert ok(client, viewer, "list_actions")["actions"] == []


async def test_admin_flow_audited_as_token_user(client, tokens):
    admin, _ = tokens
    account_id = await _fake_account(client, admin)
    fleet = ok(client, admin, "list_fleet", query="FAKE-01")
    assert [s["provider_id"] for a in fleet["accounts"] for s in a["servers"]] == ["fake-1"]
    assert ok(client, admin, "list_fleet", status="off")["accounts"] == []
    assert ok(client, admin, "list_fleet", adapter="hetzner")["accounts"] == []

    out = ok(client, admin, "rename_server", account_id=account_id,
             provider_id="fake-1", name="srv-fake-02")
    assert out["status"] == "done" and out["detail"] == "fake done"
    entries = ok(client, admin, "list_audit_log")["entries"]
    assert any(e["action"] == "rename" and e["username"] == "admin" for e in entries)
    assert any(e["action"] == "account.create" and e["username"] == "admin" for e in entries)

    # secret hygiene: the provider token never comes back out of any tool
    for name in ("list_accounts", "list_audit_log", "list_fleet", "get_overview",
                 "list_actions", "get_billing_summary", "list_allowances"):
        err, out = call(client, admin, name)
        assert TEST_TOKEN not in json.dumps(out), name


async def test_slow_action_returns_in_progress_then_completes(client, tokens):
    admin, _ = tokens
    account_id = await _fake_account(client, admin, adapter="slowfake")
    out = ok(client, admin, "power_server", account_id=account_id, provider_id="fake-1",
             kind="reboot", wait_seconds=0)
    assert out["status"] == "in_progress"
    for _ in range(50):
        row = ok(client, admin, "get_action", action_id=out["action_id"])
        if row["status"] != "in_progress":
            break
        time.sleep(0.1)
    assert row["status"] == "done"


async def test_errors_map_to_rest_status_and_text(client, tokens):
    admin, _ = tokens
    account_id = await _fake_account(client, admin)
    err, msg = call(client, admin, "get_server", account_id=account_id, provider_id="nope")
    assert err and msg.endswith("404: server not in cache")
    err, msg = call(client, admin, "power_server", account_id=999, provider_id="fake-1",
                    kind="reboot")
    assert err and msg.endswith("404: no such account")
    err, msg = call(client, admin, "power_server", account_id=account_id,
                    provider_id="fake-1", kind="power_on")
    assert err and msg.endswith("409: fake does not support power_on")
    # adapter failure: recorded as failed, surfaced as the REST 502
    err, msg = call(client, admin, "delete_server", account_id=account_id,
                    provider_id="fake-404")
    assert err and "502: AdapterError: no such server: fake-404" in msg
    err, msg = call(client, admin, "get_action", action_id=12345)
    assert err and msg.endswith("404: no such action")


def test_order_lifecycle(client, tokens):
    admin, viewer = tokens
    catalog.store("fake", [Plan(adapter="fake", name="plan-a", location="loc-1",
                                price_monthly=Money(amount="10.00", currency="EUR"))],
                  "live", None)
    plans = ok(client, viewer, "get_catalog", adapter="fake", location="loc-1")["plans"]
    assert [p["name"] for p in plans["fake"]] == ["plan-a"]
    assert ok(client, viewer, "get_catalog", location="elsewhere")["plans"].get("fake") == []

    order_id = ok(client, admin, "create_order", adapter="fake", plan_name="plan-a",
                  location="loc-1", options={"hostname": "srv-new-01"})["id"]
    ok(client, admin, "confirm_order", order_id=order_id)
    assert ok(client, viewer, "get_order", order_id=order_id)["status"] == "confirmed"
    err, msg = call(client, viewer, "execute_order", order_id=order_id)
    assert err and msg.endswith("403: Admin role required")
    assert ok(client, admin, "execute_order", order_id=order_id)["status"] == "executing"
    err, msg = call(client, admin, "cancel_order", order_id=order_id)
    assert err and msg.startswith("Error executing tool cancel_order: 409:")


def test_revoked_token_and_disabled_user_stop_working(client, tokens):
    admin, viewer = tokens
    minted = ok(client, admin, "create_api_token", name="ci")
    assert ok(client, minted["token"], "whoami")["username"] == "admin"
    # listing never returns plaintext
    assert minted["token"] not in json.dumps(ok(client, admin, "list_api_tokens"))
    ok(client, admin, "revoke_api_token", token_id=minted["id"])
    assert rpc(client, minted["token"], "tools/list").status_code == 401

    view_id = next(u["id"] for u in ok(client, admin, "list_users")["users"]
                   if u["username"] == "view")
    ok(client, admin, "update_user", user_id=view_id, disabled=True)
    assert rpc(client, viewer, "tools/list").status_code == 401
