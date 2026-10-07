# IP-change API

Your own server or script asks OmniCloud to swap one of a server's extra IPs for a fresh one. OmniCloud does the provider work and returns the new address. **Testing the new IP (DPI filtering, reachability) is your script's job.** OmniCloud never judges an IP and never rotates on a schedule.

Each server keeps one **primary** IP, which this API never touches. Only the extra ("swappable") IPs change. Give a server an extra IP first: open its Fleet dialog and use **Add IP**, or call `POST /api/servers/{account_id}/{provider_id}/ips`.

## Setup

1. **Credentials → the provider account → Purchases: on.** A change buys an IP, and accounts start with purchases off.
2. **Settings → API tokens → New token, scope `ip_change`.** Create it as an admin, because changing an IP spends money. A token with this scope can reach only the three endpoints below, so a leaked one can't delete servers or read billing.
3. Optional: **Settings → IP acquisitions per 24 h** (default 10 per account). Every add or change attempt counts, whatever its outcome, so a script stuck in a loop stops at the cap.

## Endpoints

All requests send `Authorization: Bearer <token>`.

### `GET /api/ips/{ip}`

Returns which server owns the IP, whether it can be changed, and what a change costs:

```json
{"address": "203.0.113.11", "server": "srv-ams-01", "adapter": "gcore_hosting",
 "primary": false, "changeable": true, "purchases_enabled": true,
 "cost": {"price": null, "per": "purchase", "note": "each change orders a new IP ..."},
 "acquisitions_last_24h": 2, "daily_cap": 10, "ips": [...]}
```

### `POST /api/ips/{ip}/change`

Swaps `{ip}` for a new address on the same server. The body is optional: `{"release_first": true}` releases the old IP before acquiring the new one. Use that for servers already at their extra-IP limit.

By default the new IP is acquired and attached first, and only then is the old one released. If acquiring fails, nothing has changed.

The call is synchronous and can take a few minutes while the provider provisions. Give your HTTP client a long timeout (5–10 min). If the connection drops, collect the result with `GET /api/actions/{action_id}`.

| Status | Meaning |
|---|---|
| `200` | `{"status": "done", "old_ip", "new_ip", "old_released": true, "action_id", "order_id", "cost"}`. If `old_released` is `false` and a `warning` is present, the new IP works but the old one could not be released and is still billing. |
| `202` | `{"status": "awaiting_payment", "pay_url", "provider_ref"}` (OVH). The provider created an unpaid order. Nothing is delivered or charged until someone pays at `pay_url` (always `https://`; `null` if the provider gave no safe link - pay it in the provider's panel by `provider_ref`). |
| `403` | Purchases are off for this account, or the token lacks the role or scope. |
| `404` | The IP isn't on any synced server. |
| `409` | It's the primary IP, the provider has no API to change IPs, or another IP operation is already running on this server. |
| `429` | The daily cap was reached. `detail` has `used` and `cap`. |
| `502` | The provider failed. `detail.message` states exactly what state the server was left in: "nothing changed", "old IP released, no replacement acquired", or "an IP was ordered but not assigned in time - it may still appear (and bill)". |

### `GET /api/actions/{action_id}`

Returns the outcome of a change. `result` holds the same JSON the change call returned.

## Example loop

`check_ip` is yours. Run it from a vantage point inside the filtered network.

```bash
#!/usr/bin/env bash
set -euo pipefail
PANEL=https://panel.example.test
AUTH="Authorization: Bearer $OMNI_TOKEN"
ip="$1"                                   # this server's current extra IP
for attempt in 1 2 3 4 5; do
  check_ip "$ip" && { echo "clean: $ip"; exit 0; }
  ip=$(curl -fsS --max-time 600 -X POST -H "$AUTH" "$PANEL/api/ips/$ip/change" \
       | python3 -c 'import sys,json; print(json.load(sys.stdin)["new_ip"])')
  bring_up_ip "$ip"                       # configure it on the OS if your provider needs that
done
echo "still filtered after 5 changes" >&2; exit 1
```

## What a change costs, per provider

| Provider | Extra-IP kind | A change costs | Notes |
|---|---|---|---|
| Gcore Cloud | reserved public IP | the **minutes** each IP existed | Billed per minute from creation to deletion, attached or not. Cheap to churn. A reserved IP that fails to attach is deleted at once. |
| Hetzner Cloud | Floating IP | a month's rent per new IP | Billed monthly (API spec). Configure the IP on the server's OS. |
| Gcore Hosting (BILLmanager) | additional IP | a **new IP purchase** each time | No "change IP" feature exists. Refunds for released IPs come only through a support request. Some plans cap extra IPs (KVM-SSD-1: 2), so use `release_first`. |
| OVHcloud VPS | additional (failover) IP | a month's rent per new IP | Ordered as an **unpaid** order (`202` + `pay_url`), never auto-paid. Delivered after payment. |
| LeaseWeb, Tube-hosting, LightNode | — | — | No API to add or release IPs. IPs are listed only. |
| Netlen | — | — | The API can add an IP but not release one, so changes would only pile up IPs. Not offered. |

A newly attached IP may need OS-side configuration before it answers: a hot-plugged interface on Gcore Cloud, or the floating IP added to the interface on Hetzner. Bring the address up in your script before you test it.
