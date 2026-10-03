"""Plan catalog: the marketplace core.

Providers split into LIVE catalog adapters (fetch plans from their API via
list_plans()) and SEEDED providers (curated public-pricing JSON in
server/seed_catalog/, loaded with source + last-verified stamps and never
presented as live). Storage in plan_catalog; one asyncio task per live
adapter keeps it fresh (interval from settings), reusing sync.publish().
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path

from . import db
from .adapters.base import IpOffer, Money, Plan, ProviderAdapter, TrafficCounting
from .adapters.hetzner import HetznerAdapter
from .adapters.leaseweb import LeasewebAdapter

log = logging.getLogger("omnicloud.catalog")

SEED_DIR = Path(__file__).resolve().parent / "seed_catalog"

# Registry: live adapters fetch via list_plans(); seeded adapters load from
# seed_catalog/{key}.json. Display metadata for both.
LIVE: dict[str, type[ProviderAdapter]] = {
    HetznerAdapter.key: HetznerAdapter,
    LeasewebAdapter.key: LeasewebAdapter,
}
SEEDED: dict[str, str] = {
    # key -> display name (truth-doc + seed file must exist per provider)
    "netlen": "Netlen",
    "tube": "Tube-hosting",
    "ovh": "OVHcloud",
    "gcore": "Gcore",
    "lightnode": "LightNode",
}

_tasks: dict[str, asyncio.Task] = {}


def providers_info() -> list[dict]:
    """Catalog provider list for the API/Adapters view: fleet adapters vs
    catalog providers, clearly separated."""
    out = [{"key": k, "display_name": c.display_name, "source": "live",
            "capabilities": sorted(x.value for x in c.capabilities)}
           for k, c in LIVE.items()]
    out += [{"key": k, "display_name": v, "source": "seeded",
             "capabilities": []}
            for k, v in SEEDED.items()]
    return out


# -- seed loading ---------------------------------------------------------------

def load_seed(adapter: str) -> tuple[list[Plan], dict]:
    """Curated public-pricing data, honestly stamped. Returns (plans, raw-seed
    with source_url/last_verified/billing_model). A missing/corrupt seed
    raises; the sync loop records the error per-provider."""
    path = SEED_DIR / f"{adapter}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    plans = []
    for p in raw.get("plans", []):
        pm = p.get("price_monthly") or {}
        extra = p.get("extra_ip") or {}
        plans.append(Plan(
            adapter=adapter,
            name=p["name"],
            location=p.get("location", ""),
            cpu_cores=p.get("cpu_cores"),
            ram_gb=p.get("ram_gb"),
            disk_gb=p.get("disk_gb"),
            disk_type=p.get("disk_type"),
            price_monthly=Money(amount=pm.get("amount", ""), currency=pm.get("currency", "EUR"),
                                vat_inclusive=pm.get("vat_inclusive")) if pm.get("amount") else None,
            included_traffic_bytes=p.get("included_traffic_bytes"),
            counting=(TrafficCounting(p["counting"]) if p.get("counting") else None),
            extra_ip=IpOffer(
                kind=extra.get("kind", "public ipv4"),
                included=extra.get("included", 1),
                price=Money(amount=extra["price"]["amount"],
                           currency=extra["price"].get("currency", "EUR"))
                if extra.get("price") else None,
                limit=extra.get("limit"),
                note=extra.get("note"),
            ) if extra else None,
            billing_model=raw.get("billing_model", ""),
            deprecated=p.get("deprecated", False),
        ))
    return plans, raw


# -- storage --------------------------------------------------------------------

def store(adapter: str, plans: list[Plan], source: str, last_verified: str | None) -> None:
    """Replace the adapter's catalog rows in one transaction."""
    ts = db.now()
    rows = [(adapter, p.name, p.location, p.model_dump_json(), source, last_verified, ts)
            for p in plans]
    with db.connect() as conn:
        conn.execute("DELETE FROM plan_catalog WHERE adapter=?", (adapter,))
        conn.executemany(
            """INSERT INTO plan_catalog(adapter, plan_name, location, canonical,
                                        source, last_verified, fetched_at)
               VALUES(?,?,?,?,?,?,?)""", rows)
        conn.execute(
            """INSERT INTO catalog_state(adapter, last_success_at, last_attempt_at, last_error)
               VALUES(?,?,?,NULL)
               ON CONFLICT(adapter) DO UPDATE SET last_success_at=excluded.last_success_at,
                    last_attempt_at=excluded.last_attempt_at, last_error=NULL""",
            (adapter, ts, ts))


def read() -> list[dict]:
    """All catalog rows + per-adapter state for the API."""
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT adapter, plan_name, location, canonical, source, last_verified, fetched_at
               FROM plan_catalog ORDER BY adapter, plan_name, location""").fetchall()
        state = conn.execute("SELECT * FROM catalog_state").fetchall()
    return {
        "plans": [dict(r) for r in rows],
        "state": [dict(r) for r in state],
    }


# -- sync -----------------------------------------------------------------------

async def sync_all_once() -> None:
    """One pass over every catalog provider (live fetch + seeded load)."""
    for key, cls in LIVE.items():
        # Live catalog needs an account credential (Hetzner/LeaseWeb APIs are
        # authenticated); use the first enabled account of that adapter.
        with db.connect() as conn:
            row = conn.execute(
                "SELECT a.id FROM accounts a WHERE a.adapter=? AND a.enabled=1 LIMIT 1",
                (key,)).fetchone()
        if not row:
            _record_error(key, "no enabled account; live catalog needs credentials")
            continue
        try:
            from . import accounts as accounts_mod
            account = accounts_mod.get_account(row["id"])
            adapter = accounts_mod.build_adapter(account)
            try:
                plans = await adapter.list_plans()
            finally:
                with contextlib.suppress(Exception):
                    await adapter.close()
            store(key, plans, "live", None)
            _publish(key)
        except Exception as e:  # noqa: BLE001 - one provider must not kill the pass
            _record_error(key, f"{type(e).__name__}: {e}"[:500])
    for key in SEEDED:
        try:
            result = load_seed(key)
            plans, raw = result
            store(key, plans, "seeded", raw.get("last_verified"))
            _publish(key)
        except Exception as e:  # noqa: BLE001
            _record_error(key, f"{type(e).__name__}: {e}"[:500])


def _record_error(adapter: str, msg: str) -> None:
    log.warning("catalog sync failed for %s: %s", adapter, msg)
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO catalog_state(adapter, last_attempt_at, last_error)
               VALUES(?, ?, ?)
               ON CONFLICT(adapter) DO UPDATE SET last_attempt_at=excluded.last_attempt_at,
                    last_error=excluded.last_error""",
            (adapter, db.now(), msg))


def _publish(adapter: str) -> None:
    from . import sync as sync_mod
    sync_mod.publish("catalog_updated", {"adapter": adapter})


async def _loop() -> None:
    """Provider-level catalog loop (one task, all providers, vs per-account
    fleet sync). Interval from settings, editable live."""
    while True:
        await sync_all_once()
        hours = float(db.get_setting("catalog_sync_interval_hours") or 24)
        # ponytail: single loop for all providers; per-provider tasks if one
        # slow provider ever blocks the rest
        await asyncio.sleep(max(0.25, hours) * 3600)


def start() -> None:
    if "catalog" not in _tasks:
        _tasks["catalog"] = asyncio.create_task(_loop(), name="catalog-sync")


def stop() -> None:
    t = _tasks.pop("catalog", None)
    if t:
        t.cancel()
