# Provider API Truth (verified against official docs, 2026-10-03)

Every adapter semantic below was verified against the official API
documentation (not inferred from legacy scripts). Each claim cites its
source. This file is the ground truth the adapters implement; when a
legacy workspace script disagrees with this file, this file wins.

## v2 catalog providers (verified 2026-10-03, all by direct unauthenticated curl)

### OVHcloud - LIVE catalog (tokenless)
- `GET https://eu.api.ovh.com/1.0/order/catalog/public/vps?ovhSubsidiary=FR` -
  **noAuthentication: true** (per /1.0/order.json), verified by plain curl.
  198 plans: planCode, invoiceName, pricings[] with **price in micro-cents**
  (299000000 = 2.99 EUR), intervalUnit month, capacities installation/upgrade/
  renew; configurations[] with vps_datacenter (GRA, SBG, BHS, WAW, DE, UK, SYD,
  SGP + 2026/27 EU-SOUTH-MIL, EU-WEST-RBX, YNM). No 'Starter' family; current
  families: vps-value/essential/comfort/elite/le + vps-2025/2027-modelN.
  **No traffic field anywhere in the API** - marketing says unlimited traffic
  with per-model bandwidth (500 Mbps-3 Gbps).
- `GET /1.0/order/catalog/formatted/ip?ovhSubsidiary=FR` - Additional-IP
  catalog, also tokenless: ip-failover-ripe/arin **1.99 EUR/IP/mo** (max qty
  64); blocks /30-/24 7.96-509.44 EUR/mo. VPS constraint: individual IPs only,
  **max 16 per VPS**; blocks NOT supported on VPS (Additional IP page).
- Monthly rental billing only (1/12/24-mo terms with degressive discounts);
  no hourly VPS. Full OVH fleet adapter deferred: 3-part credentials (AK/AS/CK).

### Gcore - LIVE catalog (tokenless)
- `GET https://api.gcore.com/cloud/public/v1/regions` (33 regions) and
  `.../public/v1/basic_vms/flavors?region_id=` - public, no token.
- Prices: `GET https://bff.gcore.pro/cloud/vcc-items?regionCode=` - the
  pricing-calculator BFF, tokenless; per-minute USD. **Caveat: a BFF, not a
  versioned API** - if it moves, prices degrade to not-published, never break.
- Traffic: **free and unlimited, ingress AND egress** (docs). No byte number
  exists to publish. Bandwidth capped by flavor.
- Extra public IPv4: **$2.7504/mo**, uniform across 12 tested regions
  ('externalip_min'). Bare-metal egress (the only traffic line item):
  $0.00143/GB.
- Billing: prepaid PAYG wallet, per-minute charging, ~4 USD deduction steps.

### Tube-hosting - LIVE catalog (tokenless static asset)
- `GET https://www.tube-hosting.com/assets/data/templates.json` - the exact
  data their pricing page fetches: {"kvm": [{name, price, cores, ram, disk}],
  "dedicated": ...}. Prices are **integer euro-cents** (500 = 5.00 EUR).
  Caveat: static asset, not a versioned API. Traffic and extra-IP terms not
  in the asset -> rendered not-published, verify at order time. NL-based KVM.

### Netlen - SEEDED (API exists but gated)
- Domain netlen.com.tr; public REST API v2 at api.netlen.com.tr/v2 (documented
  at netlen.com.tr/api) covering servers/firewalls/IPs - **but every endpoint,
  including GET /plans pricing, requires Bearer API key + IP allowlist**. Money
  objects are {amount, currency} decimal strings; 202 + /operations/{id}
  polling; Idempotency-Key on billing POSTs.
- Istanbul VDS plans (Xeon Platinum, 10 Gbit/s port, "Limitsiz Trafik" =
  unlimited traffic): VDS-1 $2.99, VDS-2 $3.99, VDS-3 $7.99, VDS-4 $12.99/mo.
  Extra IPv4 purchasable (subnets /29 $7.50/mo - /22 $640/mo); per-IP price
  not published. Seed = netlen.json (source + last-verified stamped).

### LightNode - SEEDED (no tokenless API)
- No public pricing API; their console API is authenticated. Seed from the
  public pricing page (2026-10-03): Start/Agency/Premium/Enterprise tiers,
  1/2/3/4 TB monthly traffic (both directions), USD monthly-tier prices
  (Start 7.71 standard regions, 10.41 HK, 17.71 Cairo). Extra IPs **not
  offered**; 1 static IPv4 included, 2 free IP changes. Hourly prepaid wallet.
  Seed = lightnode.json.

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
