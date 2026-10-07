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
  no hourly VPS.

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

## OVHcloud fleet: VPS + Public Cloud (verified 2026-10-04 against
api.ovh.com/1.0/vps.json + api.ovh.com/1.0/cloud.json, fetched live; the
identical eu.api.ovh.com spec was diffed for the VPS section; docs.ovhcloud.com
auth guides; ovhcloud.com public-cloud pricing page)

### Auth (both schemes supported; credential is a packed single string)

- **Classic API keys (AK:AS:CK)**: signature `"$1$" + SHA1_HEX(
  AS + "+" + CK + "+" + METHOD + "+" + URL + "+" + BODY + "+" + TIMESTAMP)`
  [docs.ovhcloud.com/en/guides/manage-and-operate/api/first-steps; python-ovh
  client]. Timestamp = SERVER time from `GET /1.0/auth/time` (sync a delta
  once; OVH tolerates ~30s). Headers: `X-Ovh-Application`, `X-Ovh-Consumer`,
  `X-Ovh-Timestamp`, `X-Ovh-Signature`. Hash the wire bytes actually sent -
  httpx re-serialization and the compact separators in OVH's examples are
  equivalent for this digest; hashing the sent bytes is the invariant that
  matters. Keys created at www.ovh.com/auth/api/createToken (or Control Panel
  "API keys"); `accessRules` are per method+path, e.g. `GET /cloud/project*`.
- **OAuth2 service account (client_id:client_secret)**: `POST
  https://www.ovh.com/auth/oauth2/token` with
  `grant_type=client_credentials&scope=all` -> `{access_token, expires_in:
  3599}`; then `Authorization: Bearer` on eu.api.ovh.com [docs.ovhcloud.com
  guides: authenticate-api-with-service-account, manage-service-account].
  Service accounts need an IAM policy attached (rights live on the policy,
  not the client).
- Rate limits on the /1.0 branch: **not documented anywhere official**. The
  429 backoff ladder in http.py covers it.

### VPS (`/vps`, the fixed-price product - unmetered)

- `GET /vps` returns `string[]` (serviceNames, e.g. `vps-xxxx.vps.ovh.net`);
  N+1 `GET /vps/{sn}` per VPS; **no pagination** on the /1.0 branch.
- VPS object: `name` (=serviceName, readOnly), `displayName` (**writable,
  max 50** - the rename path, via `PUT /vps/{sn}`), `state`, `zone`,
  `zoneType`, `offerType`, `model`, `vcore`, `memoryLimit`, `netbootMode`
  (local|rescue), `lockStatus{locked, reason}`, `iam{tags}`.
- State enum (exact): `backuping, installing, maintenance, rebooting,
  rescued, running, stopped, stopping, upgrading`. No "starting" state.
- `model` = `{name, offer, vcore, memory, disk, datacenter[],
  maximumAdditionnlIp, availableOptions, version}` - **bare longs with NO
  units stated in the spec** (memory/disk). Never render "GB" the API didn't
  state. No bandwidth field (per-disk `bandwidthLimit` is storage, not
  network).
- **NO traffic/bandwidth/price/labels in the VPS API at all** (unmetered
  product; unlimited-traffic marketing with per-model caps 500 Mbps-3 Gbps
  that the API doesn't expose). iam.tags are IAM-computed, not free-form
  labels. Money: not even a planCode on the service - prices live only in
  the separate /order API (see the catalog section above).
- Created/renewal: `GET /vps/{sn}/serviceInfos` (services.Service: creation,
  expiration, engagedUpTo, renewalType). IPs: `GET /vps/{sn}/ips` ->
  strings; `GET /vps/{sn}/ips/{ip}` -> `{ipAddress, version (v4|v6), type
  (primary|additional)}`.
- Actions: `POST /vps/{sn}/start|stop|reboot` (no body) -> a `vps.Task`
  synchronously: `{id, date, state, type, progress}`. Task state enum:
  `todo, doing, done, error, blocked, cancelled, paused, waitingAck` -
  poll `GET /vps/{sn}/tasks/{id}` until done. **progress is a bare long
  with no declared unit - never render it as a percentage.**
- `POST /vps/{sn}/rebuild` is **BETA** (the old /reinstall is deprecated,
  deletion 2026-10-15) - not implemented in the panel.
- **Delete is a deliberate two-step**: terminate + confirmTermination (with
  reason + commentary). Not automated: the panel refuses with a pointer to
  the OVH manager.
- `GET /vps/{sn}/status` (IP service-probe) and `GET /vps/{sn}/models` are
  DEPRECATED (deletion 2026-10-15) - not used.

### Public Cloud (`/cloud/project`)

- `GET /cloud/project` -> `string[]` serviceNames. `GET
  /cloud/project/{sn}/instance` -> Instance[] (**no pagination**): `id`
  (uuid), `name`, `region`, `status`, `flavorId`+`flavor` (name, vcpus, ram -
  **Gio per the spec's description - citable**; disk is "number of disks"),
  `ipAddresses[{ip, version, type}]`, **`currentMonthOutgoingTraffic` (long,
  BYTES - "instance outgoing network traffic for the current month")**,
  `monthlyBilling{since, status}`, `created`, `operationIds`.
- Status enum (exact, 29 values): ACTIVE, BUILD, BUILDING, DELETED,
  DELETING, ERROR, HARD_REBOOT, MIGRATING, PASSWORD, PAUSED, REBOOT,
  REBUILD, RESCUE, RESCUED, RESCUING, RESIZE, RESIZED, RESUMING,
  REVERT_RESIZE, SHELVED, SHELVED_OFFLOADED, SHELVING, SHUTOFF,
  SNAPSHOTTING, SOFT_DELETED, STOPPED, SUSPENDED, UNKNOWN, UNSHELVING,
  VERIFY_RESIZE.
- **Traffic: egress included in all locations EXCEPT Singapore (SGP1) and
  Sydney (SYD1), where 1 TB/month of outbound public traffic is included
  per Public Cloud PROJECT** (pricing page, "Public Traffic Instance"
  section); beyond that, per-GB charges apply. Inbound is always included.
  The API exposes only the running per-instance cumulative
  (currentMonthOutgoingTraffic) - no daily history, no per-instance overage
  price, no per-instance quota number (the 1 TB is per project, so
  included_bytes at the instance level is not-exposed).
- Price: **NOT on the instance or flavor objects**. Only via the regional
  listing `GET /cloud/project/{sn}/region/{regionName}/instance` ->
  InstanceList[] rows carrying `pricings[]` (`{price: {value,
  currencyCode, includeVat}, type: hour|month|licence|...}`), joined by
  instance id. An hourly-billed instance must NOT get a monthly price (never
  hourly x 730 - invented); monthly price attaches only when the instance
  reports active monthly billing.
- Actions return **void** (no task id): `POST .../instance/{id}/start|stop
  (graceful)|reboot` (reboot body `{type: "soft"}`), `DELETE
  .../instance/{id}`, rename = `PUT .../instance/{id}` `{"instanceName": ...}`.
  Confirm by polling the instance status (want ACTIVE for start/reboot,
  SHUTOFF for stop; DELETE confirmed by 404/DELETED on re-GET).

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

## Gcore Cloud fleet (verified 2026-10-05 against the official OpenAPI 3.1
spec `cloud_api.yaml` — G-Core's product-documentation repo — and
docs.gcore.com developer-tools REST API docs)

### Auth (permanent API token)

- Header `Authorization: APIKey <token>` — **NOT Bearer** (Bearer returns
  "Given token not valid for any token type"). Created at gcore.com →
  Profile → API tokens; role administrator/engineer/user, one token covers
  all products. The legacy JWT flow (`/identity/openid/token`) exists but is
  not the documented path.

### Scoping and ids

- Every regional resource path embeds project AND region:
  `/cloud/v1/instances/{project_id}/{region_id}/...`. provider_id is
  compound and self-routing: `{project}:{region}:{uuid}`.
- `GET /cloud/v1/projects` → `{count, results:[{id, name, is_default, state}]}`.
- Region ids from `GET /cloud/v1/regions` (authenticated; the tokenless
  public variant is the catalog adapter's fallback).

### Instances (InstanceSerializer — exact field names)

- List `GET /cloud/v1/instances/{p}/{r}`, envelope `{count, results}`,
  pagination `limit`/`offset` (default/max 1000).
- `name` is a **flat** field. `flavor` is **nested**:
  `flavor_id/flavor_name/vcpus/ram` (ram in **MiB**) — no top-level flavor_id.
- `addresses`: map network-name → `[{addr, type}]`; the public IP is the
  `type: "floating"` entry (`InstanceFloatingAddressSerializer`). A `fixed`
  addr cannot be told public from private (network names are user-chosen) —
  the panel renders floating only, never a guessed fixed addr.
- `created_at` (not `created`); `status` (uppercase OpenStack-style enum)
  AND `vm_state` (lowercase) both present — panel maps `status`.
- `tags`: `[{key, value, read_only}]` → labels; `read_only: true` tags are
  provider metadata (merge patch always preserves them) → facets, not
  labels.
- Status enum: ACTIVE, BUILD, DELETED, ERROR, HARD_REBOOT, MIGRATING,
  PASSWORD, PAUSED, REBOOT, REBUILD, RESCUE, RESIZE, REVERT_RESIZE, SHELVED,
  SHELVED_OFFLOADED, SHUTOFF, SOFT_DELETED, SUSPENDED, UNKNOWN, VERIFY_RESIZE.
  REBOOT/HARD_REBOOT are **not** confirmed-running — in-flight until the
  task finishes.

### Actions (all async via tasks)

- `POST /cloud/v2/instances/{p}/{r}/{id}/action` `{"action":
  "start"|"stop"|"reboot"|"reboot_hard"|"resume"|"suspend"}` → `{"tasks":
  ["<uuid>"]}`. Poll `GET /cloud/v1/tasks/{id}` (NEW/RUNNING/**FINISHED**/
  **ERROR**; ERROR carries an error string). A task id is never success;
  power actions are confirmed on the instance's own `status`
  (ACTIVE/SHUTOFF).
- Rename/relabel: `PATCH /cloud/v1/instances/{p}/{r}/{id}` accepts `name`
  and `tags` (RFC 7386 JSON Merge Patch: `key: value` adds/updates,
  `key: null` removes, `tags: null` clears all user tags; unspecified keys
  and read-only tags are always preserved), 200 + serializer — confirmed by
  the returned name/tags.
- Delete: `DELETE ...` returns **200** with `{"tasks":[...]}` (NOT 204);
  confirmed gone only when the instance GET 404s.
- **No VM rebuild endpoint in the spec** (bare metal only) — REBUILD is not
  offered. Traffic is free and unmetered (ingress AND egress) per docs —
  allowance is a window note, no byte fields.

### Pricing

- `GET /cloud/v1/pricing/{p}/{r}/instances/{id}` → `price_per_hour,
  price_per_month` (discounted), `price_without_discount_per_month,
  discount_percent, currency_code`. Panel uses the discounted
  `price_per_month`; missing/404 → None ("-", never zero).

## Gcore Hosting fleet (hosting.gcore.com - BILLmanager 6; verified 2026-10-07
against docs.ispsystem.com BILLmanager 6 API reference, docs.gcore.com
hosting articles, and the panel calls of the operator's own gcore-panel.sh)

A different product and account from Gcore Cloud: prepaid balance, servers
renew at their expiry date while the balance covers them
(docs.gcore.com/hosting/payments/renew-your-server).

- **Endpoint**: the panel URL itself, `{url}?func=<name>&out=json`. Docs list
  `out=xml|xjson|devel|text`; `out=json` is what the panel's own JS (and the
  operator's script) uses. **Auth**: `func=auth&username&password` returns
  `doc.auth.$` (session id), passed as `auth=`; valid 1 h after the last
  request. The adapter POSTs the login so the password is never in a URL.
- **Shape**: `{"doc": {...}}`, scalars `{"$": "v"}`, lists in `doc.elem` (a
  single row arrives as a bare object), errors in `doc.error.msg.$`.
- `func=vds` - servers: `id, domain, ip, pricelist, cost, expiredate,
  autoprolong, item_status` (1 ordered, 2 active, 3 suspended, 4 deleted,
  5 processing). **Service state, not VM power state** - power state is not
  exposed; `cost` carries no stated period, so it is shown verbatim, never
  as a monthly price.
- `func=service.ip elid=<server>` - IPs: `id, name (address), is_main,
  no_delete, type`. Primary = the is_main row (else no_delete; else the first
  row - protecting an extra IP by mistake is recoverable, releasing the main
  one is not).
- **Extra IPs** (docs.gcore.com .../buy-an-additional-ip-address): order
  form takes a quantity only; "after the order is processed, the new IP
  addresses appear in the IP address list" - **no preview of the address**.
  Up to 14 extra IPv4 per server; **KVM-SSD-1: 2**. No "change IP" feature;
  moving an IP between servers is a paid support request. Refunds: "contact
  support" only (.../request-a-refund). Virtual servers have a one-month
  minimum term (.../delete-a-virtual-server).
- `service.ip.edit plid=<server>` (form: slist `type`, `domain`) then the
  same func with `sok=ok` orders; `service.ip.delete elid=<ip> plid sok=ok`;
  `service.changepassword elid passwd confirm sok=ok`; `vds.delete elid
  sok=ok`.
- **Billing**: `func=payment` (status 1 new, 2 paid, 3 promised, 4 credited,
  5 awaiting refund, 6 refunded, 7 fraudulent, 8 initiated, 9 cancelled;
  `subaccountamount_iso` e.g. "9.00 EUR"); `func=subaccount` (balance) is
  documented with access level admin - a client-account refusal reads
  "not exposed".
- **Unverified until tested on a live account**: whether `count=1` on
  `service.ip.edit` is honoured; whether an IP order ever returns a
  `billorder`/payment instead of charging the balance (handled as
  awaiting_payment if it does); client access to `func=subaccount`.

## Extra IPs per provider (the IP-change API, docs/ip-change.md)

- **Gcore Cloud**: reserved fixed IPs, `type: external`
  (cloud_api.yaml; docs.gcore.com/cloud/networking/ip-address/
  create-and-configure-a-reserved-ip-address): created standalone with the
  address visible before attaching; "the price remains the same whether
  the IP is assigned or not"; "billing applies only for the time from
  creating an IP to deleting it"; cloud billing is per minute
  (docs.gcore.com/cloud/billing). Attach: `POST .../attach_interface
  {type: reserved_fixed_ip, port_id}`; detach: `.../detach_interface
  {ip_address, port_id}`; delete: `DELETE /cloud/v1/reserved_fixed_ips/{p}/
  {r}/{port_id}`. Price preview `POST /cloud/v1/pricing/{p}/{r}/
  reserved_fixed_ips`. **Unverified**: the task's `created_resources` key
  naming the new port (adapter accepts `ports` or `reserved_fixed_ips`) and
  the pricing request body.
- **Hetzner**: Floating IPs (cloud.spec.json): `POST /floating_ips {type,
  server}` returns 201 `{floating_ip, action|null}` assigned at creation;
  `DELETE /floating_ips/{id}` auto-unassigns; billed on a monthly basis;
  price per location in `GET /pricing` `floating_ips[].prices[]`. The
  server's `public_net.ipv4` is its Primary IP (max one IPv4 Primary IP per
  server).
- **OVH VPS**: `GET /vps/{sn}/ips/{ip}` -> `type: primary|additional`;
  `DELETE /vps/{sn}/ips/{ip}` releases an additional IP; ordering goes
  through the cart (`/order/vps/{sn}/ip` no longer exists): `POST
  /order/cart` -> `/assign` -> `/order/cart/{id}/ip {planCode:
  ip-failover-ripe, duration, pricingMode, quantity}` -> item configuration
  -> `/checkout {autoPayWithPreferredPaymentMethod: false}` -> `{orderId,
  url}` (unpaid). **Unverified**: configuration labels `destination` (VPS
  serviceName) and `country` (the VPS datacenter's country), duration `P1M`.
- **LeaseWeb Public Cloud**: `ips[]` on the instance (`mainIp`,
  `nullRouted`, `networkType`) - **no API to add or release an IP** (only
  list, reverse lookup and null-route). Listed only.
- **Tube-hosting, LightNode**: no add/release IP endpoint in their APIs.
  **Netlen**: `POST /servers/{id}/ips` adds (charges the balance) but no
  release endpoint exists - IP change not offered.

## Netlen, Tube-hosting, LightNode fleets (verified 2026-10-07)

### Netlen (netlen.com.tr/api, v2.0.3)
- `Authorization: Bearer <key>`; **IP allowlist mandatory** (403
  `AUTH_IP_NOT_ALLOWED`); envelope `{data, meta{pagination{page, per_page,
  total, total_pages}}}`, errors `{error{code, message, errors[]}}`.
- `GET /servers` (list rows have `power_state: null`) + `GET /servers/{id}`:
  `status active|suspended|pending|cancelled`, `power_state running|
  stopped|starting|stopping|provisioning|migrating|error|unknown`,
  `network{ipv4{address}, extra_ips[{address, version}]}`, `billing{amount,
  currency, cycle monthly|yearly, next_billing_at, deletable}`.
- `POST /servers/{id}/actions/{start|stop|reboot}` -> 202
  `data.operation{id}`; `GET /operations/{id}` queued|running|completed|
  failed.
- `POST /servers/{id}/ips {version}` -> 201 `{address, price, charged,
  balance}` - charged to the balance at once; **no release endpoint**.
  `GET /billing/balance`; invoices are panel-only (501).

### Tube-hosting (api.tube-hosting.com/docs - OpenAPI "v0", generated, unlisted)
- `POST /login {mail, password, device}` -> `{accessToken, refreshToken}`.
  The spec declares no security scheme: **Bearer header unverified**.
- `/servicegroups/currents` is typed only "object" (parsed defensively for
  nested Service objects `{id, type VPS|DEDICATED|IPV4BUNDLE|BYOIP,
  serviceGroupId, endDate, price, runtime}`); `GET /vps/{id}` -> `{coreCount,
  memory, diskSpace, osDisplayName, primaryIPv4{ipv4{ipv4}}}`; `GET
  /vps/{id}/status` -> `{status}`; `POST /vps/{id}/start|stop|shutdown|
  restart`; `PUT /vps/{id}/password {password}`.
- `GET /me` -> `balance` integer - **read as euro-cents** (the verified
  templates.json convention; unverified for /me). `GET /payments/invoices`
  -> `{id, time, finished, items[{unitPrice, quantity}]}` - no due date,
  no paid status. No IP add/release endpoint.

### LightNode (apidoc.lightnode.com)
- `x-open-token: <token>` against `https://openapi.lightnode.com`.
- `GET /region/list` -> `{regions[{regionCode, zones[{zoneCode}]}]}`; `GET
  /instance/list?regionCode&zoneCode` (both required, pageSize <= 50) ->
  `{instances[], rowCount}`; instance `{ecsResourceUUID, instanceName,
  ecsStatus (only STARTED documented), ecsPendingStatus (NONE when idle),
  publicIpAddress, secondaryPublicIpInfoList[], freeFlow (GB),
  usedFlow (unit unstated - read as GB), createTime}`.
- `POST /instance/start {ecsResourceUUID}` -> `{asyncTaskUUID}`; `GET
  /asynctask/getResult` -> `{asyncTaskInfo{taskStatus PROCESSING|FINISHED,
  processResult SUCCESS|FAIL|RETRY|CANCEL}}`. `/instance/stop` and
  `/instance/reboot` pages exist; paths assumed to mirror start.
- No IP, billing, balance or expiry endpoints.

## Billing per provider (verified 2026-10-07 against each official API spec)

- **OVHcloud** (/1.0/me.json): `GET /me/bill?date.from` -> ids; `GET
  /me/bill/{id}` -> `{billId, date, priceWithTax{value, currencyCode},
  url, pdfUrl}` (**no paid/due field on the bill itself**); `GET
  /me/bill/{id}/debt` -> `{dueAmount, dueDate, status PAID|REFUNDED|
  TO_BE_PAID|UNMATURED|UNPAID|WRITE_OFF}` (no debt record -> panel says
  "no debt"). Unpaid orders: `GET /me/order` -> `/{id}/status`
  (notPaid|checking|delivering|delivered|cancelled|...) -> `/{id}` `{date,
  expirationDate, priceWithTax, url}`. Prepaid credit: `GET
  /me/credit/balance` -> names -> `{type PREPAID_ACCOUNT|DEPOSIT|BONUS|
  VOUCHER, amount}`. Renewal: `/vps/{sn}/serviceInfos` `{expiration,
  renew{automatic}}`.
- **LeaseWeb** (Invoices API v1): `GET /invoices/v1/invoices` ->
  `{id, date, dueDate, total, openAmount, currency, status OPEN|PAID|
  READY|CANCELLED|OVERDUE}`; `GET /invoices/v1/invoices/proforma` ->
  `{total, currency, ...}` (next invoice's estimate). Post-paid: no
  balance.
- **Gcore Cloud**: **no balance or invoice endpoint** (`/iam/clients/me`
  has no balance; only `/cloud/v1/cost_report/*`, response shape not yet
  verified - not used). Prepaid PAYG wallet, charged per minute in ~4
  EUR/USD steps (docs.gcore.com/cloud/billing).
- **Hetzner Cloud**: **no billing endpoints at all** in cloud.spec.json.
- **Gcore Hosting**: see its fleet section (`func=payment`,
  `func=subaccount`, `vds.expiredate`).

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
- ovh: fleet adapter (VPS + Public Cloud) on the verified facts above; the
  packed-credential format (AK:AS:CK / client_id:client_secret) replaces the
  deferred "3-part credentials" blocker
