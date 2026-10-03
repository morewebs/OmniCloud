# Provider API Truth (verified against official docs, 2026-10-03)

Every adapter semantic below was verified against the official API
documentation (not inferred from legacy scripts). Each claim cites its
source. This file is the ground truth the adapters implement; when a
legacy workspace script disagrees with this file, this file wins.

## Hetzner Cloud (docs.hetzner.cloud + docs.hetzner.com billing/firewalls FAQ)

- **Traffic fields on the server object**: `included_traffic`, `outgoing_traffic`,
  `ingoing_traffic` are "for the current billing period" in bytes (nullable).
  The server's own `included_traffic` is **authoritative** for that server's
  current allowance (already reflects type + location + billing period).
  [cloud.spec.json]
- **`server_type.included_traffic` was REMOVED** (nulled 2024-08, removed
  2024-11). Per-location values live in `server_type.prices[]`:
  `{price_monthly {net,gross}, included_traffic, price_per_tb_traffic {net,gross}}`
  per location. [changelog 2024-07-25]
- **Server objects: `datacenter` is GONE** (deprecated 2025-12-16, removed after
  2026-07-01). Read the top-level `location` object (`name`, `country`, ...).
  `/datacenters` endpoints return 410. [changelog 2025-12-16]
- **Traffic billing**: outgoing only; incoming and internal traffic free.
  Overage billed in 100MB blocks rounded up. Notifications at 75%/100% are
  informational, not caps. [billing FAQ]
- **Overage price**: per (server_type, location) via
  `prices[].price_per_tb_traffic` (e.g. net 1.0000 / gross 1.1900 EUR); ~1 EUR/TB
  is typical, not a universal constant. [cloud.spec.json]
- **Reset window**: docs say only "current billing period"; the billing FAQ
  invoices in full calendar months. The exact counter-reset instant is NOT
  documented (see Unverifiable below). Do not claim creation-anniversary.
- **Server status enum (exact)**: `running, initializing, starting, stopping,
  off, deleting, migrating, rebuilding, unknown`. There is no `rebuild` value.
  [cloud.spec.json]
- **Action status enum (exact)**: `running | success | error` - poll
  `GET /actions/{id}` until `success`; a 201 from the action POST is never
  success. [cloud.spec.json]
- **Rate limit**: 3600 req/h per project; burst allowed; +1/sec recovery.
  Headers: `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset`
  (Reset is a UNIX timestamp of full recovery). 429 body code:
  `rate_limit_exceeded`. No `Retry-After` documented. [cloud.spec.json]
- **Firewalls are STATEFUL allow-lists**: reply traffic is auto-allowed.
  Default `in`=DROP, `out`=ACCEPT. A firewall with no inbound rule blocks
  inbound only (outbound still flows); a server with NO firewall has no
  filtering at all. Limits: 5 firewalls/server, 50 firewalls/project,
  50 rules per firewall (500 effective), ~80000 concurrent connections/server.
  [firewalls FAQ]
- **`public_net.firewalls[]`** entries carry ONLY `{id, status}` - no name.
  Join ids against `GET /firewalls` for names. [cloud.spec.json]
- **Firewall endpoints**: `POST /firewalls` -> **201** with `{firewall, actions}`;
  apply/remove take `apply_to` / `remove_from` arrays (NOT
  `remove_from_resources` as a body key) and return `{actions: [...]}` (plural).
  Rules use `source_ips` for `in`, `destination_ips` for `out`; protocols
  include tcp/udp/icmp/esp/gre; ports are strings like `"1024-5000"`.
- **`GET /servers/{id}`** returns `{server: {...}}` - unwrap the envelope.

## LeaseWeb Public Cloud (developer.leaseweb.com + LeaseWeb KB)

- **Resource**: `GET /publicCloud/v1/instances` (the legacy `/vps` product has
  its own resource with different fields - do not mix).
- **Instance fields**: `id` (uuid), `type` (e.g. `lsw.m3.large`), `resources`,
  `region` (e.g. `eu-west-3`), `reference` (human name - **there is no `name`
  field**), `startedAt`, `state`, `productType`, `ips[]`, `contract`, ... There
  is **no `datacenter`, no `pack`** on instances (those are /vps fields).
- **State enum (exact)**: `CREATING, DESTROYED, DESTROYING, FAILED, RUNNING,
  STARTING, STOPPED, STOPPING, UNKNOWN`. No POWERED_OFF/REBOOTING/etc.
- **Contract (instance)**: `{billingFrequency, term, type: HOURLY|MONTHLY}`;
  details on `GET /instances/{id}` add `state` (ACTIVE|DELETE_SCHEDULED|...),
  `endsAt`, `startsAt`. **No `dataTraffic` field** - that exists only on the
  legacy /vps contract.
- **Traffic counting: EGRESS ONLY.** KB: "Public Cloud, VPS and Object Storage
  do not charge incoming (INGRESS) traffic." (Both-direction counting applies
  to Dedicated Servers, Legacy/GP VPS, Elastic Compute, VMware, colocation.)
  `upPublic` is the billable series. **The legacy workspace's assumption that
  LeaseWeb counts both directions was wrong for Public Cloud.**
- **Metrics**: `GET /instances/{id}/metrics/datatraffic?from&to&granularity=DAY&aggregation=SUM`
  (DAY and SUM are the ONLY valid values). Response:
  `{_metadata: {summary: {downPublic: {total,...}, upPublic: {total,...}}, unit: "B"},
  metrics: {downPublic: {values: [{value, timestamp}], unit}, upPublic: {...}}}`.
  Values are integers in **bytes**.
- **Included allowance**: 1 TB **per account** (KB), not per instance; billed in
  tiers (first tier free) after that, egress only.
- **Power**: `POST /instances/{id}/start | stop | reboot` (202, empty body).
  There is NO powerOn/powerOff/shutdown endpoint. Confirm by polling state.
- **Rename**: `PUT /instances/{id}` with `{"reference": ...}` (synchronous 200).
- **Delete**: `DELETE /instances/{id}`; MONTHLY contracts REQUIRE a
  `reasonCode` body (400 without) and terminate at contract end
  (`DELETE_SCHEDULED`, `contract.endsAt`); HOURLY terminate immediately.
- **Price**: not on the instance; `GET /publicCloud/v1/instanceTypes?region=`
  exposes `{hourly, monthly}` per type; `GET /equipments/{id}/expenses`
  exposes actual per-instance spend.
- **Rate limits**: NOT documented anywhere in the developer portal. Treat as
  unknown; keep the backoff ladder.

## Unverifiable (docs do not settle these - do not encode as fact)

- Exact counter-reset instant (calendar month vs billing anniversary) for
  both providers.
- Whether any Hetzner location currently has a smaller included allowance
  (the "~1TB US" legacy claim has no official source; Hetzner's published
  data shows identical DE/US values - read the live API, don't assume).
- LeaseWeb rate-limit values and 429 behavior.
- Whether Hetzner's RateLimit-* headers appear on every response or only
  near the limit.

## Changelog of adapter fixes this research triggered

- hetzner: action poll `finished` -> `success` (was: every action would time out)
- hetzner: firewall detach body `remove_from_resources` -> `remove_from`
- hetzner: firewall action envelope `{action}` -> `{actions: []}`
- hetzner: `datacenter.location` -> top-level `location` (datacenter removed 2026)
- hetzner: allowance from server object (authoritative) with per-location catalog fallback
- hetzner: overage price from `prices[].price_per_tb_traffic` (per location), not a constant
- hetzner: status map gains starting/stopping, `rebuild` -> `rebuilding`
- hetzner: firewall facet shows id (public_net.firewalls has no name)
- leaseweb: metrics parser rewritten to the real response shape (was: null for every server)
- leaseweb: counting INGRESS_AND_EGRESS -> OUTGOING_ONLY (egress only per KB)
- leaseweb: power verbs powerOn/powerOff/shutdown -> start/stop/reboot
- leaseweb: state map replaced with the real enum; stop waits for STOPPED
- leaseweb: rename field `name` -> `reference`
- leaseweb: DELETE passes reasonCode for monthly contracts (was: 400 error)
- leaseweb: per-instance monthly price via /instanceTypes (removed from not_exposed)
- leaseweb: contract.dataTraffic dropped (only exists on legacy /vps)
