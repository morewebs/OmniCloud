"""Demo mode: serves the built SPA with canned JSON - no DB, no providers,
no login. Purely to see how the UI looks.

Run:  uv run python demo_server.py   (build first: cd web && npm run build)
Open: http://localhost:8080
"""
import asyncio
import json
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

STATIC = Path(__file__).parent / "server" / "static"

TB = 1_000_000_000_000
GB = 1_000_000_000


def server(pid, name, adapter, account, status, ip, region, stype, price,
           included, used, counting, window, facets=(), not_exposed=(),
           labels=None, created="2026-08-13T09:24:00+00:00"):
    pct = (used / included) if (included and used is not None) else None  # noqa: F841 demo
    overage = None
    if included and used is not None and used > included:
        overage = {"amount": f"{((used - included) / TB) * 1.0:.2f}", "currency": "EUR",
                   "vat_inclusive": False}
    return {
        "provider_id": pid, "name": name, "adapter": adapter, "account_id": account,
        "status": status, "ipv4": ip, "region": region, "server_type": stype,
        "created": created, "labels": labels,
        "monthly_price": price, "not_exposed": list(not_exposed),
        "allowance": None if included is None and used is None else {
            "included_bytes": included, "used_bytes": used, "counting": counting,
            "window": window, "reset_at": "2026-11-01T00:00:00+00:00",
            "overage_price": {"amount": "1.00", "currency": "EUR", "vat_inclusive": False},
            "projected_overage_cost": overage,
        },
        "facets": [{"label": l, "value": v} for l, v in facets],
    }


HZ = "outgoing traffic since the server was created this billing period"
LW = "public traffic in both directions, current calendar month"

SERVERS = [
    server("4211111", "srv-fsn1-01", "hetzner", 1, "running", "203.0.113.10",
           "fsn1 / DE", "cx22", {"amount": "3.92", "currency": "EUR", "vat_inclusive": True},
           21.99 * TB, 4.2 * TB, "outgoing_only", HZ,
           facets=[("datacenter", "fsn1-dc8")], labels={"alias": "edge-a"}),
    server("4211112", "srv-hil1-02", "hetzner", 1, "running", "203.0.113.11",
           "hil1 / FI", "cx22", {"amount": "3.92", "currency": "EUR", "vat_inclusive": True},
           21.99 * TB, 18.7 * TB, "outgoing_only", HZ),   # ~85% -> amber
    server("4211113", "srv-ash1-03", "hetzner", 1, "running", "203.0.113.12",
           "ash / US", "cx22", {"amount": "4.51", "currency": "EUR", "vat_inclusive": True},
           1.1 * TB, 1.4 * TB, "outgoing_only", HZ),      # over -> red + overage
    server("4211114", "srv-nbg1-04", "hetzner", 1, "off", "203.0.113.13",
           "nbg1 / DE", "cx11", {"amount": "3.00", "currency": "EUR", "vat_inclusive": True},
           21.99 * TB, 0, "outgoing_only", HZ),
    server("4211115", "srv-fsn1-05", "hetzner", 1, "rebuilding", "203.0.113.14",
           "fsn1 / DE", "cpx21", {"amount": "5.83", "currency": "EUR", "vat_inclusive": True},
           21.99 * TB, None, "outgoing_only", HZ),        # pending -> "-"
    server("inst-a7f3", "srv-ams1-01", "leaseweb", 2, "running", "203.0.113.20",
           "AMS-01", None, None,
           5 * TB, 0.783 * TB, "ingress_and_egress", LW,
           facets=[("pack", "g2.s.medium"), ("port speed", "1000 Mbps")],
           not_exposed=["monthly_price", "server_type", "labels"]),
    server("inst-b2c4", "srv-fra1-05", "leaseweb", 2, "unknown", "203.0.113.21",
           "FRA-10", None, None,
           1 * TB, None, None, None,                      # not reporting
           facets=[("pack", "g2.s.small")],
           not_exposed=["monthly_price", "server_type", "labels"]),
    server("inst-c9d2", "srv-fra1-06", "leaseweb", 2, "running", "203.0.113.22",
           "FRA-10", None, None,
           5 * TB, 4.7 * TB, "ingress_and_egress", LW,     # 94% -> near red
           facets=[("pack", "g2.s.medium")],
           not_exposed=["monthly_price", "server_type", "labels"]),
]

# Bulk demo servers: a large fleet to exercise pagination + search.
REGIONS = [("fsn1", "DE"), ("nbg1", "DE"), ("hel1", "FI"), ("ash", "US")]
for i in range(192):
    loc, country = REGIONS[i % 4]
    incl = 1.1 * TB if loc == "ash" else 21.99 * TB
    used = incl * (0.05 + ((i * 37) % 90) / 100)
    SERVERS.append(server(
        f"5{i:06d}", f"srv-{loc}-{i + 6:02d}", "hetzner", 1,
        "running" if i % 17 else "unknown",
        f"203.0.113.{40 + i // 2}.{i % 2}" if i < 100 else f"198.51.100.{i - 100}",
        f"{loc} / {country}", "cx22",
        {"amount": "4.51" if loc == "ash" else "3.92", "currency": "EUR",
         "vat_inclusive": True},
        incl, used, "outgoing_only", HZ,
        facets=[("datacenter", f"{loc}-dc{i % 9}")]))

NOW = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
STALE = "2026-10-03T06:12:00+00:00"  # old -> triggers visible stale decay

for i, s in enumerate(SERVERS):
    s["last_seen_at"] = STALE if s["status"] == "unknown" else NOW
    # rising traffic history for sparklines
    base = (s["allowance"] or {}).get("used_bytes") or 0
    s["traffic_history"] = [
        {"day": f"2026-10-{d:02d}", "bytes_used": int(base * f)}
        for d, f in zip(range(1, 4), (0.55, 0.75, 0.92))
    ]

FLEET = {
    "accounts": [
        {"id": 1, "adapter": "hetzner", "name": "account-a7f3", "enabled": True,
         "servers": [s for s in SERVERS if s["account_id"] == 1]},
        {"id": 2, "adapter": "leaseweb", "name": "account-b2c4", "enabled": True,
         "servers": [s for s in SERVERS if s["account_id"] == 2]},
    ],
    "sync": {
        "1": {"last_success_at": NOW, "last_error": None, "interval_minutes": 5},
        "2": {"last_success_at": NOW,
              "last_error": "AdapterError: GET /publicCloud/v1/instances: 429 - rate limited",  # noqa: E501
              "interval_minutes": 5},
    },
    "in_progress_actions": [
        {"id": 12, "account_id": 1, "provider_id": "4211115",
         "kind": "rebuild", "created_at": NOW},
    ],
}

ACCOUNTS = [
    {"id": 1, "adapter": "hetzner", "name": "account-a7f3", "enabled": 1,
     "created_at": "2026-09-01T10:00:00+00:00", "last4": "a92f", "scope": "read+write",
     "cred_created": "2026-09-01T10:00:00+00:00", "last_used_at": NOW,
     "last_success_at": NOW, "last_error": None},
    {"id": 2, "adapter": "leaseweb", "name": "account-b2c4", "enabled": 1,
     "created_at": "2026-09-25T14:30:00+00:00", "last4": "77c1", "scope": None,
     "cred_created": "2026-09-25T14:30:00+00:00", "last_used_at": NOW,
     "last_success_at": NOW, "last_error": "AdapterError: 429 rate limited"},
]

ADAPTERS = [
    {"key": "hetzner", "display_name": "Hetzner Cloud",
     "capabilities": ["delete", "firewall", "power_off", "power_on", "reboot",
                      "rebuild", "relabel", "rename", "shutdown"]},
    {"key": "leaseweb", "display_name": "LeaseWeb",
     "capabilities": ["delete", "power_off", "power_on", "reboot", "relabel",
                      "rename", "shutdown"]},
]

# Shared firewalls across batches of servers (the real Hetzner workflow).
FIREWALLS = [
    {"id": 1710054, "name": "batch-edge-fs", "rules": 3, "applied_to_count": 148,
     "applied_server_ids": [4211111, 4211112, 5000000, 5000012]},
    {"id": 1710055, "name": "batch-edge-us", "rules": 2, "applied_to_count": 24,
     "applied_server_ids": [4211113, 5000003]},
    {"id": 1710056, "name": "batch-monitor", "rules": 5, "applied_to_count": 8,
     "applied_server_ids": [5000064]},
    {"id": 1710057, "name": "batch-wireguard", "rules": 4, "applied_to_count": 41,
     "applied_server_ids": [5000001, 5000033]},
]

ALLOWANCES = [
    {"account_id": s["account_id"], "provider_id": s["provider_id"], "name": s["name"],
     "adapter": s["adapter"], "allowance": s["allowance"], "last_seen_at": s["last_seen_at"]}
    for s in SERVERS if s["allowance"]
]

BILLING = [
    {"adapter": "hetzner", "monthly_base_eur": 3.92 + 3.92 + 4.51 + 3.00 + 5.83,
     "projected_overage_eur": 0.30, "servers": 5, "price_not_exposed": False},
    {"adapter": "leaseweb", "monthly_base_eur": 0,
     "projected_overage_eur": 0.0, "servers": 3, "price_not_exposed": True},
]

ROUTES = {
    "/api/auth/status": {"needs_setup": False},
    "/api/auth/me": {"id": 1, "username": "demo-admin", "role": "admin"},
    "/api/fleet": FLEET,
    "/api/adapters": ADAPTERS,
    "/api/accounts/1/firewalls": FIREWALLS,
    "/api/accounts": ACCOUNTS,
    "/api/allowances": ALLOWANCES,
    "/api/billing/summary": BILLING,
    "/api/users": [
        {"id": 1, "username": "demo-admin", "role": "admin", "disabled": 0,
         "created_at": "2026-09-01T10:00:00+00:00"},
        {"id": 2, "username": "demo-viewer", "role": "viewer", "disabled": 0,
         "created_at": "2026-09-28T09:00:00+00:00"},
    ],
    "/api/settings": {"sync_interval:1": "5", "sync_interval:2": "15"},
    "/api/actions": [
        {"id": 12, "account_id": 1, "provider_id": "4211115", "kind": "rebuild",
         "status": "in_progress", "detail": None, "created_at": NOW,
         "completed_at": None, "username": "demo-admin"},
        {"id": 11, "account_id": 1, "provider_id": "4211112", "kind": "reboot",
         "status": "done", "detail": "reboot finished", "created_at": "2026-10-03T09:41:00+00:00",
         "completed_at": "2026-10-03T09:41:12+00:00", "username": "demo-admin"},
    ],
    "/api/audit": [
        {"id": 3, "action": "reboot", "target": "hetzner/account-a7f3/4211112",
         "before_state": '{"status": "running"}', "after_state": '{"status": "running"}',
         "created_at": NOW, "username": "demo-admin"},
        {"id": 2, "action": "account.create", "target": "leaseweb/account-b2c4",
         "before_state": None, "after_state": None,
         "created_at": "2026-09-25T14:30:00+00:00", "username": "demo-admin"},
    ],
}

app = FastAPI()


@app.get("/api/stream")
async def stream():
    async def gen():
        while True:
            await asyncio.sleep(30)
            yield ": ping\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                            headers={"Cache-Control": "no-cache"})


@app.get("/api/fleet/{account_id}/{provider_id}")
def server_detail(account_id: int, provider_id: str):
    s = next((x for x in SERVERS
              if x["account_id"] == account_id and x["provider_id"] == provider_id), None)
    return s or {}


@app.get("/api/openapi.json")
def openapi():
    return {"openapi": "3.1.0", "info": {"title": "OmniCloud demo"}, "paths": {}}


@app.get("/api/{path:path}")
def mock(path: str):
    return ROUTES.get(f"/api/{path}", {})


app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")


@app.get("/{path:path}")
def spa(path: str):
    f = STATIC / path
    if f.is_file():
        return FileResponse(f)
    return FileResponse(STATIC / "index.html")


if __name__ == "__main__":
    import uvicorn
    print("Demo running: http://localhost:8080  (mock data, Ctrl+C to stop)")
    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="warning")
