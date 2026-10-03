"""The entire REST surface. One router, sections by comment."""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import accounts, audit, auth, catalog, config, db, orders, secrets, sync, update, version
from .adapters.base import Capability

router = APIRouter(prefix="/api")


# -- CSRF: require X-Requested-With on all non-GET /api requests --------------
# Enforced in main.py as app-level middleware (a router can't carry middleware).

from fastapi.responses import JSONResponse  # noqa: E402


async def enforce_csrf(request: Request, call_next):
    if request.url.path.startswith("/api") and request.method != "GET" \
            and request.headers.get("x-requested-with") != "XMLHttpRequest":
        return JSONResponse({"detail": "missing X-Requested-With header"}, 403)
    return await call_next(request)


# -- auth --------------------------------------------------------------------

class SetupBody(BaseModel):
    username: str
    password: str


@router.get("/auth/status")
def auth_status():
    """Whether first-run setup is needed (no users yet)."""
    return {"needs_setup": auth.user_count() == 0}


@router.post("/auth/setup")
def setup(body: SetupBody, response: Response):
    uid = auth.create_first_admin(body.username, body.password)
    if uid is None:
        raise HTTPException(403, "setup is closed - an admin already exists")
    audit.record(uid, "user.create", f"user/{body.username}")
    token, _ = auth.login(body.username, body.password)
    _set_cookie(response, token)
    return {"ok": True, "role": "admin"}


class LoginBody(BaseModel):
    username: str
    password: str


# ponytail: in-memory per-username+IP backoff; a shared store if this ever
# runs multi-worker
_login_fails: dict[str, tuple[int, float]] = {}  # key -> (fails, locked_until)
_MAX_FAILS, _LOCK_SECONDS = 5, 15 * 60


def _login_key(body: LoginBody, request: Request) -> str:
    ip = request.client.host if request.client else "?"
    return f"{body.username}|{ip}"


@router.post("/auth/login")
def login(body: LoginBody, response: Response, request: Request):
    import time as _time
    key = _login_key(body, request)
    fails, locked_until = _login_fails.get(key, (0, 0.0))
    if _time.time() < locked_until:
        remaining = int((locked_until - _time.time()) / 60) + 1
        raise HTTPException(429, f"too many failed attempts - try again in {remaining} min")
    result = auth.login(body.username, body.password)
    if not result:
        _login_fails[key] = (fails + 1,
                             _time.time() + _LOCK_SECONDS if fails + 1 >= _MAX_FAILS else 0.0)
        raise HTTPException(401, "wrong username or password")
    _login_fails.pop(key, None)
    token, user = result
    _set_cookie(response, token)
    return {"ok": True, "role": user.role, "username": user.username}


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(auth.COOKIE)
    if token:
        auth.logout(token)
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


@router.get("/auth/me")
def me(user: auth.User = Depends(auth.require_user)):
    return {"id": user.id, "username": user.username, "role": user.role}


class PasswordBody(BaseModel):
    current_password: str
    new_password: str


@router.patch("/auth/me")
def change_password(body: PasswordBody, response: Response,
                    user: auth.User = Depends(auth.require_user)):
    if not auth.verify_credentials(user.username, body.current_password):
        raise HTTPException(403, "current password is wrong")
    with db.connect() as conn:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                     (auth.hash_password(body.new_password), user.id))
    # a changed password kills every session - the caller signs in again
    auth.revoke_sessions(user.id)
    response.delete_cookie(auth.COOKIE)
    return {"ok": True, "relogin": True}


def _set_cookie(response: Response, token: str) -> None:
    # secure flag: default ON (production posture). OMNICLOUD_COOKIE_SECURE=0
    # only for plain-HTTP local dev - see DEPLOY.md.
    secure = os.environ.get("OMNICLOUD_COOKIE_SECURE", "1") not in ("0", "false")
    response.set_cookie(auth.COOKIE, token, httponly=True, samesite="strict",
                        secure=secure, max_age=config.SESSION_TTL_DAYS * 86400)


# -- fleet --------------------------------------------------------------------

@router.get("/fleet")
def fleet(user: auth.User = Depends(auth.require_user)):
    with db.connect() as conn:
        accts = conn.execute(
            "SELECT id, adapter, name, enabled FROM accounts ORDER BY id").fetchall()
        srv = conn.execute(
            "SELECT account_id, provider_id, canonical, last_seen_at FROM servers").fetchall()
        inflight = conn.execute(
            "SELECT id, account_id, provider_id, kind, created_at FROM actions "
            "WHERE status='in_progress' ORDER BY id").fetchall()
    by_acct: dict[int, list] = {}
    for row in srv:
        s = json.loads(row["canonical"])
        s["last_seen_at"] = row["last_seen_at"]
        by_acct.setdefault(row["account_id"], []).append(s)
    with db.connect() as conn:
        sync_rows = conn.execute(
            "SELECT account_id, last_success_at, last_error FROM sync_state").fetchall()
    sync_info = {}
    for r in sync_rows:
        sync_info[str(r["account_id"])] = {
            "last_success_at": r["last_success_at"],
            "last_error": r["last_error"],
            "interval_minutes": accounts.interval_for(r["account_id"], ""),
        }
    return {
        "accounts": [
            {
                "id": a["id"], "adapter": a["adapter"], "name": a["name"],
                "enabled": bool(a["enabled"]),
                "servers": by_acct.get(a["id"], []),
            }
            for a in accts
        ],
        "sync": sync_info,
        "in_progress_actions": [dict(r) for r in inflight],
    }


@router.get("/fleet/{account_id}/{provider_id}")
def fleet_server(account_id: int, provider_id: str,
                 user: auth.User = Depends(auth.require_user)):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT canonical, last_seen_at FROM servers WHERE account_id=? AND provider_id=?",
            (account_id, provider_id),
        ).fetchone()
        hist = conn.execute(
            "SELECT day, bytes_used FROM traffic_history "
            "WHERE account_id=? AND provider_id=? ORDER BY day",
            (account_id, provider_id),
        ).fetchall()
    if not row:
        raise HTTPException(404, "server not in cache")
    s = json.loads(row["canonical"])
    s["last_seen_at"] = row["last_seen_at"]
    s["traffic_history"] = [dict(h) for h in hist]
    # Firewall names from cached facets (id lookup happens live when the
    # firewall dialogs open - the firewalls endpoint carries ids + which
    # servers each is applied to).
    s["firewalls"] = [
        {"id": None, "name": f["value"]}
        for f in s.get("facets", []) if f["label"] == "firewall"
    ]
    return s


# -- allowances / billing ------------------------------------------------------

@router.get("/allowances")
def allowances(user: auth.User = Depends(auth.require_user)):
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT account_id, provider_id, canonical, last_seen_at FROM servers").fetchall()
    out = []
    for row in rows:
        s = json.loads(row["canonical"])
        if s.get("allowance"):
            out.append({
                "account_id": row["account_id"],
                "provider_id": row["provider_id"],
                "name": s["name"],
                "adapter": s["adapter"],
                "allowance": s["allowance"],
                "last_seen_at": row["last_seen_at"],
            })
    return out


@router.get("/billing/summary")
def billing_summary(user: auth.User = Depends(auth.require_user)):
    """Spend grouped per (adapter, currency) - EUR and USD are never silently
    summed (the same rule /api/overview follows)."""
    with db.connect() as conn:
        rows = conn.execute("SELECT account_id, canonical FROM servers").fetchall()
    totals: dict[tuple, dict] = {}
    for row in rows:
        s = json.loads(row["canonical"])
        cur = (s.get("monthly_price") or {}).get("currency", "EUR")
        key = (s["adapter"], cur)
        t = totals.setdefault(key, {"adapter": s["adapter"], "currency": cur,
                                    "monthly_base": 0.0, "projected_overage": 0.0,
                                    "servers": 0, "price_not_exposed": False})
        t["servers"] += 1
        mp = s.get("monthly_price")
        if mp:
            t["monthly_base"] += float(mp["amount"])
        else:
            t["price_not_exposed"] = True
        al = s.get("allowance") or {}
        po = al.get("projected_overage_cost")
        if po:
            t["projected_overage"] += float(po["amount"])
    return list(totals.values())


# -- accounts / credentials -----------------------------------------------------

class AccountBody(BaseModel):
    adapter: str
    name: str
    token: str
    scope: str | None = None


@router.get("/accounts")
def get_accounts(user: auth.User = Depends(auth.require_user)):
    return accounts.list_accounts()


@router.post("/accounts")
def create_account(body: AccountBody, _=Depends(auth.require_admin)):
    try:
        account_id = accounts.create_account(body.adapter, body.name, body.token, body.scope)
    except secrets.SecretsUnavailable as e:
        raise HTTPException(503, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit.record(_.id, "account.create", f"{body.adapter}/{body.name}")
    sync.restart_account(account_id)
    return {"id": account_id}


class AccountPatch(BaseModel):
    enabled: bool | None = None
    name: str | None = None


@router.patch("/accounts/{account_id}")
def patch_account(account_id: int, body: AccountPatch, user=Depends(auth.require_admin)):
    if not accounts.get_account(account_id):
        raise HTTPException(404, "no such account")
    if body.enabled is not None:
        accounts.set_enabled(account_id, body.enabled)
        if body.enabled:
            sync.restart_account(account_id)
        else:
            sync.stop_account(account_id)
    if body.name is not None:
        with db.connect() as conn:
            conn.execute("UPDATE accounts SET name=? WHERE id=?", (body.name, account_id))
    audit.record(user.id, "account.update", f"account/{account_id}")
    return {"ok": True}


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, user=Depends(auth.require_admin)):
    if not accounts.delete_account(account_id):
        raise HTTPException(404, "no such account")
    sync.stop_account(account_id)
    audit.record(user.id, "account.delete", f"account/{account_id}")
    return {"ok": True}


@router.post("/accounts/{account_id}/sync")
def force_sync(account_id: int, user=Depends(auth.require_user)):
    # admin-only? No: a viewer refreshing data is harmless and useful.
    sync.request_sync(account_id)
    return {"ok": True}


# -- adapters --------------------------------------------------------------------

@router.get("/adapters")
def adapters(user: auth.User = Depends(auth.require_user)):
    """Fleet adapters (account-backed) + catalog providers (marketplace-only)
    in one response, each with its catalog source."""
    return accounts.adapter_info() + catalog.providers_info()


# -- actions ----------------------------------------------------------------------

class ActionBody(BaseModel):
    kind: str
    params: dict = {}


@router.post("/servers/{account_id}/{provider_id}/actions")
async def run_action(account_id: int, provider_id: str, body: ActionBody,
                     user: auth.User = Depends(auth.require_admin)):
    account = accounts.get_account(account_id)
    if not account:
        raise HTTPException(404, "no such account")
    adapter_cls = accounts.ADAPTERS.get(account["adapter"])
    try:
        cap = Capability(body.kind)
    except ValueError:
        raise HTTPException(400, f"unknown action kind: {body.kind}")
    if adapter_cls and cap not in adapter_cls.capabilities:
        # 409: capability absent from this adapter - rendered as absent in UI,
        # this check is for direct API users.
        raise HTTPException(409, f"{account['adapter']} does not support {body.kind}")
    import asyncio
    asyncio.get_event_loop()  # ensure loop exists (TestClient thread)
    action_id = await sync.run_action(account_id, provider_id, body.kind,
                                      user.id, body.params)
    with db.connect() as conn:
        row = conn.execute("SELECT status, detail FROM actions WHERE id=?", (action_id,)).fetchone()
    if row["status"] == "failed":
        raise HTTPException(502, row["detail"] or "action failed")
    return {"action_id": action_id, "status": row["status"], "detail": row["detail"]}


class FirewallBody(BaseModel):
    rules: list[dict]
    attach: bool = True


@router.get("/accounts/{account_id}/firewalls")
async def list_firewalls(account_id: int, user: auth.User = Depends(auth.require_user)):
    """Existing firewalls on the account (shared resources, batches of servers).
    Powers the attach/detach workflow; a large fleet has many."""
    account = accounts.get_account(account_id)
    if not account:
        raise HTTPException(404, "no such account")
    adapter = accounts.build_adapter(account)
    try:
        fws = await adapter.list_firewalls()
    except AttributeError:
        raise HTTPException(409, f"{account['adapter']} does not support firewall management")
    finally:
        import contextlib
        with contextlib.suppress(Exception):
            await adapter.close()
    return fws


@router.post("/accounts/{account_id}/firewalls/{firewall_id}/attach/{provider_id}")
async def attach_firewall(account_id: int, firewall_id: int, provider_id: str,
                          user: auth.User = Depends(auth.require_admin)):
    account = accounts.get_account(account_id)
    if not account:
        raise HTTPException(404, "no such account")
    adapter = accounts.build_adapter(account)
    try:
        await adapter.attach_firewall(firewall_id, provider_id)
    finally:
        import contextlib
        with contextlib.suppress(Exception):
            await adapter.close()
    audit.record(user.id, "firewall.attach",
                 f"{account['adapter']}/{account['name']}/{provider_id}/fw-{firewall_id}")
    with contextlib.suppress(Exception):
        await sync.sync_account_now(account_id)
    return {"ok": True}


@router.post("/accounts/{account_id}/firewalls/{firewall_id}/detach/{provider_id}")
async def detach_firewall(account_id: int, firewall_id: int, provider_id: str,
                          user: auth.User = Depends(auth.require_admin)):
    account = accounts.get_account(account_id)
    if not account:
        raise HTTPException(404, "no such account")
    adapter = accounts.build_adapter(account)
    try:
        await adapter.detach_firewall(firewall_id, provider_id)
    finally:
        import contextlib
        with contextlib.suppress(Exception):
            await adapter.close()
    audit.record(user.id, "firewall.detach",
                 f"{account['adapter']}/{account['name']}/{provider_id}/fw-{firewall_id}")
    with contextlib.suppress(Exception):
        await sync.sync_account_now(account_id)
    return {"ok": True}


@router.post("/servers/{account_id}/{provider_id}/firewall")
async def apply_firewall(account_id: int, provider_id: str, body: FirewallBody,
                         user: auth.User = Depends(auth.require_admin)):
    account = accounts.get_account(account_id)
    if not account:
        raise HTTPException(404, "no such account")
    adapter_cls = accounts.ADAPTERS.get(account["adapter"])
    if not adapter_cls or Capability.FIREWALL not in adapter_cls.capabilities:
        raise HTTPException(409, f"{account['adapter']} does not support firewall management")
    from .adapters.base import UnsupportedAction
    try:
        adapter = accounts.build_adapter(account)
        try:
            await adapter.apply_firewall(provider_id, body.rules, body.attach)
        finally:
            import contextlib
            with contextlib.suppress(Exception):
                await adapter.close()
        audit.record(user.id, "firewall.apply",
                     f"{account['adapter']}/{account['name']}/{provider_id}")
        import asyncio as _a
        with contextlib.suppress(Exception):
            await sync.sync_account_now(account_id)
        return {"ok": True}
    except UnsupportedAction as e:
        raise HTTPException(409, str(e))


@router.get("/actions")
def list_actions(user: auth.User = Depends(auth.require_user)):
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT a.id, a.account_id, a.provider_id, a.kind, a.status, a.detail,
                      a.created_at, a.completed_at, u.username
               FROM actions a LEFT JOIN users u ON u.id = a.requested_by
               ORDER BY a.id DESC LIMIT 100"""
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/audit")
def list_audit(user: auth.User = Depends(auth.require_admin)):
    return audit.list_entries()


# -- catalog ------------------------------------------------------------------------

@router.get("/catalog")
def get_catalog(user: auth.User = Depends(auth.require_user)):
    """All plans grouped by provider + per-adapter source/staleness state."""
    data = catalog.read()
    grouped: dict[str, list] = {}
    for row in data["plans"]:
        p = json.loads(row["canonical"])
        p["source"] = row["source"]
        p["last_verified"] = row["last_verified"]
        p["fetched_at"] = row["fetched_at"]
        grouped.setdefault(row["adapter"], []).append(p)
    return {
        "providers": catalog.providers_info(),
        "plans": grouped,
        "state": data["state"],
    }


@router.post("/catalog/sync")
async def catalog_sync(user: auth.User = Depends(auth.require_admin)):
    """Force a catalog refresh now (live fetch + seeded reload)."""
    await catalog.sync_all_once()
    return {"ok": True}


@router.get("/catalog/images")
async def catalog_images(adapter: str, user: auth.User = Depends(auth.require_user)):
    """Orderable OS images for the order dialog (live adapters only)."""
    if adapter not in catalog.LIVE:
        return []
    with db.connect() as conn:
        row = conn.execute("SELECT a.id FROM accounts a WHERE a.adapter=? AND a.enabled=1 "
                           "LIMIT 1", (adapter,)).fetchone()
    if not row:
        return []
    account = accounts.get_account(row["id"])
    inst = accounts.build_adapter(account)
    import contextlib as _cl
    try:
        return await inst.list_images()
    finally:
        with _cl.suppress(Exception):
            await inst.close()


@router.get("/catalog/providers")
def catalog_providers(user: auth.User = Depends(auth.require_user)):
    return catalog.providers_info()


# -- orders -------------------------------------------------------------------------

class OrderBody(BaseModel):
    adapter: str
    plan_name: str
    location: str
    options: dict = {}


@router.post("/orders")
def create_order(body: OrderBody, user: auth.User = Depends(auth.require_admin)):
    try:
        order_id = orders.create_order(user.id, body.adapter, body.plan_name,
                                       body.location, body.options)
    except orders.OrderError as e:
        raise HTTPException(400, str(e))
    return {"id": order_id, "status": "draft"}


@router.get("/orders")
def list_orders(user: auth.User = Depends(auth.require_user)):
    return orders.list_orders()


@router.get("/orders/{order_id}")
def get_order(order_id: int, user: auth.User = Depends(auth.require_user)):
    o = orders.get_order(order_id)
    if not o:
        raise HTTPException(404, "no such order")
    return o


@router.post("/orders/{order_id}/confirm")
def confirm_order(order_id: int, user: auth.User = Depends(auth.require_admin)):
    try:
        orders.confirm(order_id, user.id)
    except orders.OrderError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: int, user: auth.User = Depends(auth.require_admin)):
    try:
        orders.cancel(order_id, user.id)
    except orders.OrderError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@router.post("/orders/{order_id}/execute")
def execute_order(order_id: int, user: auth.User = Depends(auth.require_admin)):
    try:
        orders.execute(order_id, user.id)
    except orders.OrderError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "status": "executing"}


# -- overview -------------------------------------------------------------------------

@router.get("/overview")
def overview(user: auth.User = Depends(auth.require_user)):
    """Dashboard aggregate: status counts, per-currency spend, traffic totals,
    recent actions, alerts. Never sums mixed currencies."""
    with db.connect() as conn:
        rows = conn.execute("SELECT canonical FROM servers").fetchall()
        hist = conn.execute(
            "SELECT day, SUM(bytes_used) AS total FROM traffic_history "
            "GROUP BY day ORDER BY day DESC LIMIT 30").fetchall()
        actions = conn.execute(
            """SELECT a.id, a.kind, a.status, a.detail, a.created_at, u.username
               FROM actions a LEFT JOIN users u ON u.id = a.requested_by
               ORDER BY a.id DESC LIMIT 10""").fetchall()
        order_rows = conn.execute(
            """SELECT o.id, o.status, o.adapter, o.plan_name, o.estimated_monthly, o.created_at
               FROM orders o ORDER BY o.id DESC LIMIT 5""").fetchall()
        sync_rows = conn.execute("SELECT * FROM sync_state").fetchall()

    status_counts: dict[str, int] = {}
    # spend grouped per (adapter, currency) - mixed currencies never summed
    spend: dict[str, dict[str, float]] = {}
    alerts: list[dict] = []
    for row in rows:
        s = json.loads(row["canonical"])
        status_counts[s.get("status", "unknown")] = status_counts.get(s.get("status", "unknown"), 0) + 1
        mp = s.get("monthly_price")
        if mp:
            cur = mp.get("currency", "EUR")
            spend.setdefault(s["adapter"], {}).setdefault(cur, 0.0)
            spend[s["adapter"]][cur] += float(mp["amount"])
        al = s.get("allowance") or {}
        inc, used = al.get("included_bytes"), al.get("used_bytes")
        if inc and used is not None and used / inc > 0.8:
            alerts.append({"kind": "allowance", "server": s["name"],
                           "adapter": s["adapter"],
                           "pct": round(used / inc * 100)})
    for sr in sync_rows:
        if sr["last_error"]:
            alerts.append({"kind": "sync", "account_id": sr["account_id"],
                           "error": sr["last_error"][:200]})

    traffic_days = [{"day": h["day"], "bytes": h["total"]} for h in reversed(hist)]
    return {
        "fleet": {"total": len(rows), "by_status": status_counts},
        "spend": spend,  # {adapter: {currency: amount}}
        "projected_overage": _overage_per_currency(rows),
        "traffic_days": traffic_days,
        "recent_actions": [dict(a) for a in actions],
        "recent_orders": [dict(o) for o in order_rows],
        "alerts": alerts,
    }


def _overage_per_currency(rows) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows:
        s = json.loads(row["canonical"])
        po = (s.get("allowance") or {}).get("projected_overage_cost")
        if po:
            cur = po.get("currency", "EUR")
            out[cur] = out.get(cur, 0.0) + float(po["amount"])
    return out


# -- users ------------------------------------------------------------------------

class UserBody(BaseModel):
    username: str
    password: str
    role: str = "viewer"


@router.get("/users")
def list_users(user: auth.User = Depends(auth.require_admin)):
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, username, role, disabled, created_at FROM users ORDER BY id").fetchall()
    return [dict(r) for r in rows]


@router.post("/users")
def create_user(body: UserBody, user: auth.User = Depends(auth.require_admin)):
    if body.role not in ("admin", "viewer"):
        raise HTTPException(400, "role must be admin or viewer")
    uid = auth.create_user(body.username, body.password, body.role)
    audit.record(user.id, "user.create", f"user/{body.username}")
    return {"id": uid}


class UserPatch(BaseModel):
    role: str | None = None
    disabled: bool | None = None
    password: str | None = None


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch, user: auth.User = Depends(auth.require_admin)):
    if body.role is not None and body.role not in ("admin", "viewer"):
        raise HTTPException(400, "role must be admin or viewer")
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "no such user")
        if body.role is not None:
            conn.execute("UPDATE users SET role=? WHERE id=?", (body.role, user_id))
        if body.disabled is not None:
            conn.execute("UPDATE users SET disabled=? WHERE id=?", (int(body.disabled), user_id))
        if body.password is not None:
            conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                         (auth.hash_password(body.password), user_id))
    if body.disabled or body.password is not None:
        # a reset password or a disabled account kills the target's sessions
        auth.revoke_sessions(user_id)
    audit.record(user.id, "user.update", f"user/{row['username']}")
    return {"ok": True}


# -- settings ---------------------------------------------------------------------

@router.get("/settings")
def get_settings(user: auth.User = Depends(auth.require_admin)):
    with db.connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings WHERE key LIKE 'sync_%' "
                            "OR key LIKE 'update_%'").fetchall()
    return {r["key"]: r["value"] for r in rows}


@router.put("/settings")
def put_settings(body: dict, user: auth.User = Depends(auth.require_admin)):
    for k, v in body.items():
        if not (k.startswith("sync_") or k.startswith("update_")):
            raise HTTPException(400, "only sync_* and update_* settings are editable")
        db.set_setting(k, str(v))
    audit.record(user.id, "settings.update", "settings")
    return {"ok": True}


# -- SSE ---------------------------------------------------------------------------

@router.get("/stream")
async def stream(request: Request, user: auth.User = Depends(auth.require_user)):
    q = await sync.subscribe()
    import asyncio

    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=30)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            sync.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                            headers={"Cache-Control": "no-cache",
                                     "X-Accel-Buffering": "no"})


@router.get("/health")
# -- updates ------------------------------------------------------------------

@router.get("/update/status")
def update_status(user: auth.User = Depends(auth.require_user)):
    return update.status()


@router.post("/update/check")
async def update_check(user: auth.User = Depends(auth.require_user)):
    return await update.check()


@router.post("/update/apply")
async def update_apply(user: auth.User = Depends(auth.require_admin)):
    try:
        return await update.apply()
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@router.get("/health")
def health():
    """Liveness + readiness: the DB must actually answer."""
    try:
        with db.connect() as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"database unavailable: {type(e).__name__}")
    return {"ok": True, "version": version.VERSION,
            "schema_version": db.SCHEMA_VERSION}
