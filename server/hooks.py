"""Traffic-allowance hooks: POST an HTTP API when a server's traffic
allowance crosses a threshold.

Rides the per-account sync loop (sync._sync_once calls after_sync with the
fresh canonical rows). Hysteresis is level-based per (hook, server), ordered
none < warn < over: a hook fires only on an upward crossing, and re-arms as
usage falls back below the threshold - a new billing window re-fires
naturally, a counter hovering at the threshold does not spam.

A FAILED delivery leaves the armed level where it was, so the next sync
retries the same crossing; a delivered one fires exactly once per crossing.
Level-based arming means a warn whose delivery failed is superseded by the
over crossing, not queued behind it.

Header values are secrets like provider tokens: stored, sent, never returned
by any API (listings carry the header NAMES only). The extra object is
merged into the delivered payload as-is - its keys win over the built-ins.
"""
from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import urlsplit

import httpx

from . import db

log = logging.getLogger("omnicloud.hooks")

LEVELS = ("none", "warn", "over")
EVENT_NAMES = {"warn": "allowance_warn", "over": "allowance_over", "test": "test"}
# between the 3 delivery attempts; tests monkeypatch to zero
RETRY_DELAYS = (0.5, 2.0)
TIMEOUT_S = 10.0

# strong refs so spawned deliveries can't be garbage-collected mid-POST
_inflight: set[asyncio.Task] = set()


# -- evaluation ----------------------------------------------------------------

def _match(hook: dict, server: dict) -> bool:
    """The hook's filters (adapter, account, provider), AND-combined; a null
    filter matches every server."""
    return ((not hook["adapter"] or server.get("adapter") == hook["adapter"])
            and (hook["account_id"] is None
                 or server.get("account_id") == hook["account_id"])
            and (not hook["provider_id"]
                 or server.get("provider_id") == hook["provider_id"]))


def _server_level(server: dict, hook: dict) -> str | None:
    """The highest hook-enabled level the server's allowance is at. None =
    no measurable allowance (unmetered or not exposed - unknown, never
    zero, so never a crossing)."""
    al = server.get("allowance") or {}
    used, included = al.get("used_bytes"), al.get("included_bytes")
    if used is None or not included:
        return None
    pct = used / included * 100
    events = hook["events"]
    if "over" in events and pct >= 100:
        return "over"
    if "warn" in events and pct >= hook["warn_pct"]:
        return "warn"
    return "none"


def _armed_levels(hook_id: int) -> dict[tuple, str]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT account_id, provider_id, level FROM hook_armed WHERE hook_id=?",
            (hook_id,)).fetchall()
    return {(r["account_id"], r["provider_id"]): r["level"] for r in rows}


def _set_armed(hook_id: int, account_id: int, provider_id: str, level: str) -> None:
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO hook_armed(hook_id, account_id, provider_id, level, updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(hook_id, account_id, provider_id) DO UPDATE SET
                 level=excluded.level, updated_at=excluded.updated_at""",
            (hook_id, account_id, provider_id, level, db.now()))


async def after_sync(rows: list[dict]) -> None:
    """Evaluate every enabled hook against one account's fresh rows and
    spawn deliveries for upward crossings. Never raises into the sync loop."""
    try:
        hooks = load_hooks(enabled_only=True)
        for hook in hooks:
            armed = _armed_levels(hook["id"])
            for server in rows:
                if not _match(hook, server):
                    continue
                level = _server_level(server, hook)
                if level is None:
                    continue
                key = (server["account_id"], server["provider_id"])
                prev = armed.get(key, "none")
                if LEVELS.index(level) > LEVELS.index(prev):
                    _spawn(hook, level, server)
                elif level != prev:
                    # usage fell back below the armed level: re-arm now so
                    # the next rise fires again
                    _set_armed(hook["id"], key[0], key[1], level)
    except Exception:  # noqa: BLE001 - a hook bug must never kill the sync loop
        log.warning("hook evaluation failed", exc_info=True)


def _spawn(hook: dict, event: str, server: dict) -> None:
    task = asyncio.create_task(deliver(hook, event, server))
    _inflight.add(task)
    task.add_done_callback(_inflight.discard)


# -- delivery --------------------------------------------------------------------

def build_payload(hook: dict, event: str, server: dict) -> dict:
    al = server.get("allowance") or {}
    used, included = al.get("used_bytes"), al.get("included_bytes")
    pct = round(used / included * 100, 1) if used is not None and included else None
    out = {
        "event": EVENT_NAMES.get(event, event),
        "hook": {"id": hook["id"], "name": hook["name"]},
        "fired_at": db.now(),
        "server": {
            "account_id": server.get("account_id"),
            "provider_id": server.get("provider_id"),
            "name": server.get("name"),
            "adapter": server.get("adapter"),
            "ipv4": server.get("ipv4"),
        },
        "allowance": {**al, "pct": pct},
    }
    out.update(hook.get("extra") or {})
    return out


async def deliver(hook: dict, event: str, server: dict, http=None) -> dict:
    """POST one payload to the hook's URL (3 attempts on network errors,
    429 and 5xx), record the delivery, raise the armed level on success.
    `http` injects the test's client (MockTransport); production creates
    and closes its own."""
    body = json.dumps(build_payload(hook, event, server))
    headers = {"Content-Type": "application/json", **(hook.get("headers") or {})}
    owned = http is None
    client = httpx.AsyncClient(timeout=TIMEOUT_S) if owned else http
    last_error: str | None = None
    last_status: int | None = None
    try:
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                r = await client.post(hook["url"], content=body, headers=headers)
                last_status = r.status_code
                if 200 <= r.status_code < 300:
                    return _record(hook, event, server, "delivered", r.status_code, None)
                if r.status_code != 429 and r.status_code < 500:
                    # the receiver rejected the request itself: retrying
                    # the identical POST cannot help
                    return _record(hook, event, server, "failed", r.status_code,
                                   f"HTTP {r.status_code}")
                last_error = f"HTTP {r.status_code}"
            except httpx.HTTPError as e:  # noqa: PERF203 - the retry loop is the point
                last_status = None
                last_error = f"{type(e).__name__}: {e}"
            if attempt < len(RETRY_DELAYS):
                await asyncio.sleep(RETRY_DELAYS[attempt])
    finally:
        if owned:
            await client.aclose()
    return _record(hook, event, server, "failed", last_status, last_error)


def _record(hook: dict, event: str, server: dict, status: str,
            response_status: int | None, error: str | None) -> dict:
    row = {
        "hook_id": hook["id"], "event": event,
        "account_id": server.get("account_id"),
        "provider_id": server.get("provider_id"),
        "server_name": server.get("name"),
        "status": status, "response_status": response_status, "error": error,
    }
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO hook_deliveries(hook_id, event, account_id, provider_id,
                                           server_name, status, response_status, error, created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (hook["id"], event, server.get("account_id"), server.get("provider_id"),
             server.get("name"), status, response_status, error, db.now()))
        if event in ("warn", "over") and status == "delivered":
            conn.execute(
                """INSERT INTO hook_armed(hook_id, account_id, provider_id, level, updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(hook_id, account_id, provider_id) DO UPDATE SET
                     level=excluded.level, updated_at=excluded.updated_at""",
                (hook["id"], row["account_id"], row["provider_id"], event, db.now()))
    from . import sync  # late: sync imports this module at load time
    sync.publish("hook_delivered", {
        "hook_id": hook["id"], "event": event, "status": status,
        "server": server.get("name"),
    })
    return row


# -- test delivery -----------------------------------------------------------------

async def send_test(hook: dict, http=None) -> dict:
    """One real POST with event "test": the first cached server matching the
    hook's filters, or a synthetic sample when nothing matches. Never arms,
    so it can be repeated freely."""
    server = _matching_server(hook) or _sample_server(hook)
    return await deliver(hook, "test", server, http=http)


def _matching_server(hook: dict) -> dict | None:
    with db.connect() as conn:
        if hook["account_id"] is not None:
            rows = conn.execute(
                "SELECT canonical FROM servers WHERE account_id=?",
                (hook["account_id"],)).fetchall()
        else:
            rows = conn.execute("SELECT canonical FROM servers").fetchall()
    for row in rows:
        server = json.loads(row["canonical"])
        if _match(hook, server):
            return server
    return None


def _sample_server(hook: dict) -> dict:
    return {
        "account_id": hook["account_id"] if hook["account_id"] is not None else 0,
        "provider_id": hook["provider_id"] or "sample-server",
        "name": "sample (no matching server)",
        "adapter": hook["adapter"] or "sample",
        "ipv4": None,
        "allowance": {
            "used_bytes": 18_000_000_000_000,
            "included_bytes": 21_990_000_000_000,
            "counting": "outgoing_only",
            "window": "calendar month (sample values)",
        },
    }


# -- CRUD ---------------------------------------------------------------------------

def load_hooks(enabled_only: bool = False) -> list[dict]:
    """All hooks with events/headers/extra parsed. Internal shape - header
    values included; never hand this to an API response."""
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM traffic_hooks ORDER BY id").fetchall()
    hooks = []
    for r in rows:
        if enabled_only and not r["enabled"]:
            continue
        hooks.append(_parse_hook(r))
    return hooks


def _parse_hook(r) -> dict:
    return {
        "id": r["id"], "name": r["name"], "url": r["url"],
        "events": json.loads(r["events"]),
        "warn_pct": r["warn_pct"],
        "adapter": r["adapter"], "account_id": r["account_id"],
        "provider_id": r["provider_id"],
        "headers": json.loads(r["headers"]) if r["headers"] else {},
        "extra": json.loads(r["extra"]) if r["extra"] else {},
        "enabled": bool(r["enabled"]),
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


def get_hook(hook_id: int) -> dict | None:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM traffic_hooks WHERE id=?", (hook_id,)).fetchone()
    return _parse_hook(row) if row else None


def _validate(url: str, events: list[str], warn_pct: float, headers: dict | None,
              extra: dict | None, account_id: int | None) -> dict:
    """Clean and check one hook's editable fields; raises ValueError with a
    message fit for the UI."""
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("url must be a valid http(s) URL")
    if not events or not set(events) <= {"warn", "over"}:
        raise ValueError("events must be a non-empty list of 'warn' and/or 'over'")
    if not 1 <= warn_pct <= 100:
        raise ValueError("warn_pct must be between 1 and 100")
    if headers:
        if not isinstance(headers, dict) or not all(
                isinstance(k, str) and k and isinstance(v, str) for k, v in headers.items()):
            raise ValueError("headers must be an object of string: string")
    if extra is not None and not isinstance(extra, dict):
        raise ValueError("extra must be a JSON object")
    if account_id is not None:
        with db.connect() as conn:
            if not conn.execute("SELECT 1 FROM accounts WHERE id=?",
                                (account_id,)).fetchone():
                raise ValueError(f"no account {account_id}")
    return {
        "url": url,
        "events": json.dumps([e for e in ("warn", "over") if e in events]),
        "warn_pct": float(warn_pct),
        "headers": json.dumps(headers) if headers else None,
        "extra": json.dumps(extra) if extra else None,
        "account_id": account_id,
    }


def create_hook(name: str, url: str, events: list[str], warn_pct: float,
                adapter: str | None, account_id: int | None,
                provider_id: str | None, headers: dict | None,
                extra: dict | None) -> int:
    if not name or not name.strip():
        raise ValueError("name must not be empty")
    v = _validate(url, events, warn_pct, headers, extra, account_id)
    ts = db.now()
    with db.connect() as conn:
        cur = conn.execute(
            """INSERT INTO traffic_hooks(name, url, events, warn_pct, adapter,
                                         account_id, provider_id, headers, extra,
                                         enabled, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,1,?,?)""",
            (name.strip(), v["url"], v["events"], v["warn_pct"], adapter or None,
             account_id, provider_id or None, v["headers"], v["extra"], ts, ts))
        return cur.lastrowid


def update_hook(hook_id: int, patch: dict) -> None:
    """Apply a partial update. `patch` keys are the column names; a null
    adapter/account_id/provider_id or empty headers/extra CLEARS the filter
    (send absent keys to keep them)."""
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM traffic_hooks WHERE id=?", (hook_id,)).fetchone()
    if not row:
        raise KeyError(hook_id)
    merged = {
        "name": row["name"], "url": row["url"],
        "events": json.loads(row["events"]), "warn_pct": row["warn_pct"],
        "adapter": row["adapter"], "account_id": row["account_id"],
        "provider_id": row["provider_id"],
        "headers": json.loads(row["headers"]) if row["headers"] else {},
        "extra": json.loads(row["extra"]) if row["extra"] else {},
    }
    if "name" in patch:
        if not patch["name"] or not str(patch["name"]).strip():
            raise ValueError("name must not be empty")
        merged["name"] = str(patch["name"]).strip()
    if "url" in patch:
        merged["url"] = patch["url"]
    if "events" in patch and patch["events"] is not None:
        merged["events"] = patch["events"]
    if "warn_pct" in patch and patch["warn_pct"] is not None:
        merged["warn_pct"] = patch["warn_pct"]
    if "adapter" in patch:
        merged["adapter"] = patch["adapter"] or None
    if "account_id" in patch:
        merged["account_id"] = patch["account_id"]
    if "provider_id" in patch:
        merged["provider_id"] = patch["provider_id"] or None
    if "headers" in patch:
        merged["headers"] = patch["headers"] or {}
    if "extra" in patch:
        merged["extra"] = patch["extra"] or {}
    v = _validate(merged["url"], merged["events"], merged["warn_pct"],
                  merged["headers"], merged["extra"], merged["account_id"])
    if "enabled" in patch and patch["enabled"] is not None:
        enabled = 1 if patch["enabled"] else 0
    else:
        enabled = row["enabled"]
    with db.connect() as conn:
        conn.execute(
            """UPDATE traffic_hooks SET name=?, url=?, events=?, warn_pct=?, adapter=?,
                                       account_id=?, provider_id=?, headers=?, extra=?,
                                       enabled=?, updated_at=? WHERE id=?""",
            (merged["name"], v["url"], v["events"], v["warn_pct"], merged["adapter"],
             merged["account_id"], merged["provider_id"], v["headers"], v["extra"],
             enabled, db.now(), hook_id))


def delete_hook(hook_id: int) -> bool:
    with db.connect() as conn:
        cur = conn.execute("DELETE FROM traffic_hooks WHERE id=?", (hook_id,))
        return cur.rowcount > 0


def _public(hook: dict, last_delivery: dict | None) -> dict:
    """API shape: everything except header VALUES (secret - names only)."""
    return {
        "id": hook["id"], "name": hook["name"], "url": hook["url"],
        "events": hook["events"], "warn_pct": hook["warn_pct"],
        "adapter": hook["adapter"], "account_id": hook["account_id"],
        "provider_id": hook["provider_id"],
        "header_names": sorted(hook["headers"]),
        "extra": hook["extra"] or None,
        "enabled": hook["enabled"],
        "created_at": hook["created_at"], "updated_at": hook["updated_at"],
        "last_delivery": last_delivery,
    }


def list_hooks() -> list[dict]:
    hooks = load_hooks()
    last: dict[int, dict] = {}
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM hook_deliveries WHERE id IN "
            "(SELECT MAX(id) FROM hook_deliveries GROUP BY hook_id)").fetchall()
    for r in rows:
        last[r["hook_id"]] = {
            "event": r["event"], "status": r["status"],
            "response_status": r["response_status"], "error": r["error"],
            "created_at": r["created_at"],
        }
    return [_public(h, last.get(h["id"])) for h in hooks]


def list_deliveries(hook_id: int | None = None, limit: int = 100) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    with db.connect() as conn:
        if hook_id is not None:
            rows = conn.execute(
                """SELECT d.*, h.name AS hook_name FROM hook_deliveries d
                   LEFT JOIN traffic_hooks h ON h.id = d.hook_id
                   WHERE d.hook_id=? ORDER BY d.id DESC LIMIT ?""",
                (hook_id, limit)).fetchall()
        else:
            rows = conn.execute(
                """SELECT d.*, h.name AS hook_name FROM hook_deliveries d
                   LEFT JOIN traffic_hooks h ON h.id = d.hook_id
                   ORDER BY d.id DESC LIMIT ?""", (limit,)).fetchall()
    return [dict(r) for r in rows]
