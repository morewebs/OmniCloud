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
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
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
    {"key": "ovh", "display_name": "OVHcloud",
     "capabilities": ["delete", "power_off", "power_on", "reboot", "rename",
                      "shutdown"]},
    {"key": "gcore", "display_name": "Gcore",
     "capabilities": ["delete", "power_off", "power_on", "reboot", "relabel",
                      "rename", "shutdown"]},
    {"key": "tube", "display_name": "Tube-hosting", "capabilities": [],
     "source": "live"},
    {"key": "netlen", "display_name": "Netlen", "capabilities": [],
     "source": "seeded"},
    {"key": "lightnode", "display_name": "LightNode", "capabilities": [],
     "source": "seeded"},
]

# Shared firewalls across batches of servers (the real Hetzner workflow).
# rule_detail mirrors the adapter passthrough: Hetzner exposes rules, other
# adapters would omit the key (the UI then shows "not exposed").
FIREWALLS = [
    {"id": 1710054, "name": "batch-edge-fs", "rules": 3, "applied_to_count": 148,
     "applied_server_ids": [4211111, 4211112, 5000000, 5000012],
     "rule_detail": [
         {"direction": "in", "protocol": "tcp", "port": "22",
          "source_ips": ["203.0.113.0/24"]},
         {"direction": "in", "protocol": "tcp", "port": "80,443",
          "source_ips": []},
         {"direction": "out", "protocol": "tcp", "port": "",
          "source_ips": []},
     ]},
    {"id": 1710055, "name": "batch-edge-us", "rules": 2, "applied_to_count": 24,
     "applied_server_ids": [4211113, 5000003],
     "rule_detail": [
         {"direction": "in", "protocol": "tcp", "port": "22",
          "source_ips": ["198.51.100.7"]},
         {"direction": "in", "protocol": "tcp", "port": "443",
          "source_ips": []},
     ]},
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
    {"adapter": "hetzner", "currency": "EUR", "monthly_base": 3.92 + 3.92 + 4.51 + 3.00 + 5.83,
     "projected_overage": 0.30, "servers": 5, "price_not_exposed": False},
    {"adapter": "leaseweb", "currency": "EUR", "monthly_base": 0,
     "projected_overage": 0.0, "servers": 3, "price_not_exposed": True},
]

# ---- v2: catalog + orders + overview demo data --------------------------

def _plan(adapter, name, location, cpu, ram, disk, price, cur="EUR",
          traffic=None, traffic_note=None, ip_price=None, ip_note=None, billing="", source="live"):
    return {
        "adapter": adapter, "name": name, "location": location,
        "cpu_cores": cpu, "cpu_arch": "x86", "ram_gb": ram, "disk_gb": disk,
        "disk_type": "nvme",
        "price_monthly": {"amount": price, "currency": cur, "vat_inclusive": True},
        "price_hourly": None,
        "included_traffic_bytes": traffic,
        "counting": "outgoing_only" if traffic else None,
        "overage_price": {"amount": "1.19", "currency": "EUR", "vat_inclusive": True} if traffic else None,
        "traffic_note": traffic_note,
        "extra_ip": {"kind": "floating", "included": 1, "price": (
            {"amount": ip_price, "currency": cur, "vat_inclusive": True} if ip_price else None),
            "limit": 5, "note": ip_note},
        "billing_model": billing, "deprecated": False,
        "source": source, "last_verified": "2026-10-03" if source == "seeded" else None,
    }

CATALOG = {
    "providers": [
        {"key": "hetzner", "display_name": "Hetzner Cloud", "source": "live",
         "capabilities": ["delete", "firewall", "power_on", "power_off", "reboot",
                          "rebuild", "relabel", "rename", "shutdown"]},
        {"key": "leaseweb", "display_name": "LeaseWeb", "source": "live",
         "capabilities": ["delete", "power_on", "power_off", "reboot", "relabel", "rename", "shutdown"]},
        {"key": "ovh", "display_name": "OVHcloud", "source": "live",
         "capabilities": ["delete", "power_on", "power_off", "reboot", "rename", "shutdown"]},
        {"key": "gcore", "display_name": "Gcore", "source": "live",
         "capabilities": ["delete", "power_on", "power_off", "reboot", "relabel",
                          "rename", "shutdown"]},
        {"key": "tube", "display_name": "Tube-hosting", "source": "live", "capabilities": []},
        {"key": "netlen", "display_name": "Netlen", "source": "seeded", "capabilities": []},
        {"key": "lightnode", "display_name": "LightNode", "source": "seeded", "capabilities": []},
    ],
    "plans": {
        "hetzner": [
            _plan("hetzner", "cx22", "fsn1", 2, 4, 40, "3.92", traffic=21.99 * TB,
                  ip_note="floating IPs; price not published in the API",
                  billing="monthly invoice or prepaid credit"),
            _plan("hetzner", "cx22", "ash", 2, 4, 40, "4.51", traffic=1.1 * TB,
                  ip_note="floating IPs; price not published in the API",
                  billing="monthly invoice or prepaid credit"),
            _plan("hetzner", "cpx21", "fsn1", 3, 4, 80, "5.89", traffic=21.99 * TB,
                  ip_note="floating IPs; price not published in the API",
                  billing="monthly invoice or prepaid credit"),
            _plan("hetzner", "ccx13", "nbg1", 2, 8, 80, "8.79", traffic=21.99 * TB,
                  ip_note="floating IPs; price not published in the API",
                  billing="monthly invoice or prepaid credit"),
        ],
        "leaseweb": [
            _plan("leaseweb", "lsw.m3.small", "eu-west-3", 1, 4, 50, "8.40",
                  ip_note="additional IPs orderable; price not published in the API",
                  billing="monthly invoice (term) or hourly prepaid"),
            _plan("leaseweb", "lsw.m3.medium", "eu-west-3", 2, 8, 80, "14.90",
                  ip_note="additional IPs orderable; price not published in the API",
                  billing="monthly invoice (term) or hourly prepaid"),
        ],
        "ovh": [
            _plan("ovh", "vps-value-1-2-40", "GRA", 1, 2, 40, "5.80",
                  traffic_note="unlimited traffic (fair-use); bandwidth by model",
                  ip_price="1.99",
                  ip_note="Additional IP (RIPE); max 16 per VPS",
                  billing="monthly invoice, 1/12/24-month terms"),
            _plan("ovh", "vps-essential-2-4-40", "GRA", 2, 4, 40, "11.30",
                  traffic_note="unlimited traffic (fair-use)",
                  ip_price="1.99",
                  ip_note="Additional IP (RIPE); max 16 per VPS",
                  billing="monthly invoice, 1/12/24-month terms"),
            _plan("ovh", "vps-2027-model1", "SBG", 2, 4, 40, "4.49",
                  traffic_note="unlimited traffic; 500 Mbps",
                  ip_price="1.99", ip_note="Additional IP (RIPE); max 16 per VPS",
                  billing="monthly invoice, 1/12/24-month terms"),
        ],
        "gcore": [
            _plan("gcore", "g2s-shared-1-1-25", "FRN-2", 1, 1, 25, "46.22", cur="USD",
                  traffic_note="unmetered (free ingress and egress)",
                  ip_price="2.75",
                  ip_note="public IPv4",
                  billing="prepaid pay-as-you-go wallet (per-minute)"),
            _plan("gcore", "g3a-standard-2-4-50", "FRN-2", 2, 4, 50, "19.30", cur="USD",
                  traffic_note="unmetered (free ingress and egress)",
                  ip_price="2.75", ip_note="public IPv4",
                  billing="prepaid pay-as-you-go wallet (per-minute)"),
        ],
        "tube": [
            _plan("tube", "Starter", "NL", 2, 4, 30, "5.00",
                  ip_note="offered; price not published - verify at order",
                  billing="prepaid"),
            _plan("tube", "Advanced", "NL", 4, 8, 60, "9.00",
                  ip_note="offered; price not published - verify at order",
                  billing="prepaid"),
        ],
        "netlen": [
            _plan("netlen", "VDS-1", "istanbul", 1, 1, 15, "2.99", cur="USD",
                  traffic_note="unlimited traffic, 10 Gbit/s port",
                  ip_note="extra IPv4 purchasable; per-IP price not published",
                  billing="monthly invoice (USD) or hourly", source="seeded"),
            _plan("netlen", "VDS-3", "istanbul", 2, 4, 60, "7.99", cur="USD",
                  traffic_note="unlimited traffic, 10 Gbit/s port",
                  ip_note="extra IPv4 purchasable; per-IP price not published",
                  billing="monthly invoice (USD) or hourly", source="seeded"),
            _plan("netlen", "VDS-4", "istanbul", 4, 8, 120, "12.99", cur="USD",
                  traffic_note="unlimited traffic, 10 Gbit/s port",
                  ip_note="extra IPv4 purchasable; per-IP price not published",
                  billing="monthly invoice (USD) or hourly", source="seeded"),
        ],
        "lightnode": [
            _plan("lightnode", "Start", "istanbul", 2, 2, 50, "7.71", cur="USD",
                  traffic=1 * TB, ip_note="not offered as add-on; 1 static IPv4 included",
                  billing="hourly prepaid (wallet)", source="seeded"),
            _plan("lightnode", "Premium", "istanbul", 8, 8, 200, "27.70", cur="USD",
                  traffic=3 * TB, billing="hourly prepaid (wallet)", source="seeded"),
            _plan("lightnode", "Start", "hong-kong", 2, 2, 50, "10.41", cur="USD",
                  traffic=1 * TB, billing="hourly prepaid (wallet)", source="seeded"),
        ],
    },
    "state": [
        {"adapter": k, "last_success_at": NOW, "last_error": None}
        for k in ("hetzner", "leaseweb", "ovh", "gcore", "tube", "netlen", "lightnode")
    ],
}

def _order(id_, status, adapter, plan, loc, est, cur, extra=0, mode="prototype"):
    return {
        "id": id_, "mode": mode, "status": status, "adapter": adapter,
        "account_id": 1, "plan_name": plan, "location": loc,
        "options": f'{{"extra_ips": {extra}}}',
        "plan_snapshot": "{}",
        "estimated_monthly": f'{{"amount": "{est}", "currency": "{cur}", "partial": false}}',
        "resulting_provider_id": f"proto-{id_}" if status == "provisioned" else None,
        "requested_by": 1, "username": "demo-admin",
        "created_at": NOW, "updated_at": NOW,
    }

ORDERS = [
    _order(3, "confirmed", "hetzner", "cx22", "fsn1", "3.92", "EUR"),
    _order(2, "provisioned", "lightnode", "std-2-4", "fra", "9.90", "USD", extra=1),
    _order(1, "cancelled", "gcore", "g1-standard-1-2", "ams", "5.00", "USD"),
]

TRAFFIC_DAYS = [
    {"day": f"2026-10-{d:02d}", "bytes": int((28 + i * 2.3) * TB)}
    for i, d in enumerate(range(1, 4))
]

# per-day spend by currency, first_seen-gated like the real endpoint: the
# fleet's EUR base is the same every day here (no new priced servers added)
SPEND_DAYS = [
    {"day": d["day"], "spend": {"EUR": 836.0}} for d in TRAFFIC_DAYS
]

OVERVIEW = {
    "fleet": {"total": 200, "by_status": {"running": 188, "unknown": 11, "off": 1}},
    "spend": {"hetzner": {"EUR": 743.6}, "leaseweb": {"EUR": 92.4}},
    "projected_overage": {"EUR": 0.30},
    "traffic_days": TRAFFIC_DAYS,
    "spend_days": SPEND_DAYS,
    "recent_actions": [
        {"id": 12, "kind": "rebuild", "status": "in_progress", "detail": None,
         "created_at": NOW, "username": "demo-admin"},
        {"id": 11, "kind": "reboot", "status": "done", "detail": "reboot success",
         "created_at": "2026-10-03T09:41:00+00:00", "username": "demo-admin"},
        {"id": 10, "kind": "firewall.attach", "status": "done", "detail": None,
         "created_at": "2026-10-03T08:12:00+00:00", "username": "demo-admin"},
    ],
    "recent_orders": [
        {"id": 3, "status": "confirmed", "adapter": "hetzner", "plan_name": "cx22",
         "estimated_monthly": '{"amount": "3.92", "currency": "EUR"}', "created_at": NOW},
        {"id": 2, "status": "provisioned", "adapter": "lightnode", "plan_name": "std-2-4",
         "estimated_monthly": '{"amount": "9.90", "currency": "USD"}', "created_at": NOW},
    ],
    "alerts": [
        {"kind": "down", "server": "srv-nbg1-04", "adapter": "hetzner", "status": "off"},
        {"kind": "down", "server": "srv-fra1-05", "adapter": "leaseweb", "status": "unknown"},
        {"kind": "allowance", "server": "srv-hil1-02", "adapter": "hetzner", "pct": 85},
        {"kind": "allowance", "server": "srv-ash1-03", "adapter": "hetzner", "pct": 127},
        {"kind": "sync", "account_id": 2, "error": "AdapterError: 429 rate limited"},
    ],
}

ROUTES = {
    "/api/auth/status": {"needs_setup": False},
    "/api/auth/me": {"id": 1, "username": "demo-admin", "role": "admin"},
    "/api/fleet": FLEET,
    "/api/adapters": ADAPTERS,
    "/api/catalog": CATALOG,
    "/api/catalog/providers": CATALOG["providers"],
    "/api/orders": ORDERS,
    "/api/overview": OVERVIEW,
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
    "/api/auth/tokens": [
        {"id": 1, "name": "backup-script", "created_at": "2026-09-20T12:00:00+00:00",
         "last_used_at": "2026-10-04T06:00:00+00:00"},
    ],
    "/api/update/status": {"current": "0.4.2", "latest": None, "repo": "morewebs/OmniCloud",
                           "checked_at": "2026-10-03T09:00:00+00:00", "notes": None,
                           "url": None, "error": None, "applying": False},
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


# -- personal API tokens: mutable so the Settings panel works in the demo.
# Registered before the /api/{path} catch-all - FastAPI matches in order. --
TOKENS = list(ROUTES["/api/auth/tokens"])  # start from the seed rows


@app.get("/api/auth/tokens")
def list_tokens():
    return TOKENS


@app.post("/api/auth/tokens")
async def create_token(body: dict):
    tid = max([t["id"] for t in TOKENS], default=0) + 1
    import secrets
    TOKENS.insert(0, {"id": tid, "name": body.get("name", "token"),
                     "created_at": NOW, "last_used_at": None})
    # plaintext is shown exactly once - like the real server
    return {"id": tid, "name": body.get("name", "token"),
            "token": secrets.token_urlsafe(32)}


@app.delete("/api/auth/tokens/{tid}")
def revoke_token(tid: int):
    global TOKENS
    if not any(t["id"] == tid for t in TOKENS):
        return JSONResponse({"detail": "no such token"}, status_code=404)
    TOKENS = [t for t in TOKENS if t["id"] != tid]
    return {"ok": True}


@app.get("/api/{path:path}")
def mock(path: str):
    # live order detail: route to the mutable ORDERS list
    if path.startswith("orders/"):
        try:
            oid = int(path.split("/")[1])
            o = next(o for o in ORDERS if o["id"] == oid)
        except (ValueError, StopIteration):
            return JSONResponse({"detail": "no such order"}, status_code=404)
        o = dict(o)
        o["events"] = EVENTS.get(oid, [])
        return o
    return ROUTES.get(f"/api/{path}", {})


# mutable demo order state so the pipeline can be driven from the UI
EVENTS: dict[int, list] = {
    3: [{"status": "draft", "detail": None, "created_at": NOW},
        {"status": "confirmed", "detail": None, "created_at": NOW}],
    2: [{"status": "draft", "detail": None, "created_at": NOW},
        {"status": "confirmed", "detail": None, "created_at": NOW},
        {"status": "executing", "detail": "prototype - no server created",
         "created_at": NOW},
        {"status": "provisioned", "detail": None, "created_at": NOW}],
}


@app.post("/api/orders")
async def create_order(body: dict):
    plan = next((p for p in CATALOG["plans"][body["adapter"]]
                 if p["name"] == body["plan_name"]), None)
    if not plan:
        return JSONResponse({"detail": "no such plan"}, status_code=400)
    oid = max(o["id"] for o in ORDERS) + 1
    est = plan["price_monthly"]
    extra = body.get("options", {}).get("extra_ips", 0)
    if extra and plan.get("extra_ip") and plan["extra_ip"].get("price"):
        amt = float(est["amount"]) + extra * float(plan["extra_ip"]["price"]["amount"])
        est = {**plan["price_monthly"], "amount": f"{amt:.2f}"}
    o = _order(oid, "draft", body["adapter"], body["plan_name"],
               body["location"], est["amount"], est["currency"], extra=extra)
    ORDERS.insert(0, o)
    EVENTS[oid] = [{"status": "draft", "detail": None, "created_at": NOW}]
    return {"id": oid, "status": "draft"}


@app.post("/api/orders/{oid}/confirm")
async def confirm_order(oid: int):
    return _transition(oid, {"confirmed"} if _status(oid) == "draft" else set())


@app.post("/api/orders/{oid}/cancel")
async def cancel_order(oid: int):
    allowed = {"draft", "confirmed"} if _status(oid) in ("draft", "confirmed") else set()
    return _transition(oid, allowed)


@app.post("/api/orders/{oid}/execute")
async def execute_order(oid: int):
    s = _status(oid)
    if s != "confirmed":
        return JSONResponse({"detail": "cannot execute"}, status_code=409)
    _transition(oid, {"executing"})
    await asyncio.sleep(3)  # visible intermediate state, like the real prototype
    _transition(oid, {"provisioned"})
    return {"ok": True}


def _status(oid: int) -> str:
    o = next((o for o in ORDERS if o["id"] == oid), None)
    if not o:
        raise LookupError(oid)
    return o["status"]


def _transition(oid: int, allowed: set) -> dict:
    """Apply the transition if allowed; 409 with the real pipeline's message."""
    s = _status(oid)
    if not allowed:
        return JSONResponse({"detail": f"cannot transition from {s}"}, status_code=409)
    o = next(o for o in ORDERS if o["id"] == oid)
    o["status"] = next(iter(allowed))
    o["updated_at"] = NOW
    if o["status"] == "provisioned":
        o["resulting_provider_id"] = f"proto-{oid}"
    EVENTS.setdefault(oid, []).append(
        {"status": o["status"], "detail": None, "created_at": NOW})
    return {"ok": True}


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
