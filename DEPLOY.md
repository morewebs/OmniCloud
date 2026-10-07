# Deploying OmniCloud

Single-process by design (uvicorn, one worker): the SSE hub, sync loops, and
login rate-limiter are in-process state. Run one process per install; it
comfortably serves a small ops team over a 200+ server fleet.

## Quick production run

```bash
uv sync
export OMNICLOUD_MASTER_KEY=$(uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
cd web && npm install && npm run build && cd ..   # SPA into server/static/
uv run uvicorn server.main:app --host 0.0.0.0 --port 8000
```

First visit shows the one-time setup screen (creates the first admin; it
closes forever after).

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `OMNICLOUD_MASTER_KEY` | *(empty)* | Fernet key encrypting provider credentials (API tokens and panel logins). Without it the app runs but credential operations return 503. **Back this up separately from the DB** — losing it loses every stored credential. |
| `OMNICLOUD_DB` | `./omnicloud.db` | SQLite path (WAL mode). Put it on a real disk, not a container overlay you never back up. |
| `OMNICLOUD_SESSION_TTL_DAYS` | `30` | Session lifetime. |
| `OMNICLOUD_SYNC_INTERVAL_MIN` | `5` | Default fleet sync interval (per-account override in Settings). |
| `OMNICLOUD_COOKIE_SECURE` | `1` | Session cookie `Secure` flag. Set `0` **only** for plain-HTTP local dev; behind TLS keep `1`. |
| `OMNICLOUD_UPDATE_REPO` | `morewebs/OmniCloud` | Repo the update panel checks (`owner/name`). |
| `OMNICLOUD_TRUST_PROXY` | `0` | Set `1` behind a reverse proxy: rate-limit by `X-Forwarded-For` instead of the proxy IP. |

## Backups

`omnicloud.db` holds Fernet-encrypted provider credentials — it *is* the install.
SQLite's online backup is safe while the app runs:

```bash
sqlite3 omnicloud.db ".backup '/backup/omnicloud-$(date +%F).db'"
```

Back up `OMNICLOUD_MASTER_KEY` and the DB **together** (a DB without its key
is unreadable; a key without the DB is useless). Test a restore before you
need one: `cp backup.db omnicloud.db && uv run uvicorn server.main:app`.

## Reverse proxy

SSE (`/api/stream`) needs unbuffered pass-through — nginx:

```nginx
location /api/stream {
    proxy_pass http://127.0.0.1:8000;
    proxy_buffering off;
    proxy_read_timeout 1h;
}
location /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_read_timeout 960s;   # server actions poll the provider up to ~15 min
}
location /mcp {
    proxy_pass http://127.0.0.1:8000;
    proxy_read_timeout 660s;   # action tools wait up to wait_seconds (max 600)
}
location / { proxy_pass http://127.0.0.1:8000; }
```

`proxy_read_timeout 960s` matters: a rebuild/delete waits for the provider's
own confirmation (up to 900s), and an IP change waits for the provider to
assign the new address (minutes on BILLmanager panels). A proxy that cuts at
60s returns a 502 to the operator while the action still completes
server-side - an IP-change caller recovers the outcome from
`GET /api/actions/{id}`. `/mcp` answers plain JSON (no SSE stream to
unbuffer); its action tools return `in_progress` after `wait_seconds`
(default 50) and the agent polls `get_action`.

## Provider-side setup

- **Netlen** API keys work only from allow-listed IPs: add this panel's
  outgoing IP to the key's allowlist, or every sync fails with
  `AUTH_IP_NOT_ALLOWED`.
- **LightNode** issues API tokens after a manual review in its console.
- **Gcore Hosting** and **Tube-hosting** have no API tokens: the panel signs
  in with the account login. Consider a dedicated login where the provider
  offers sub-users.
- **Gcore Hosting panel URL** (another BILLmanager panel works too) must be
  a public `https://` host on port 443, without `user@` in it. A host that
  resolves to a loopback, private, link-local or other internal address is
  refused before the login is sent - point it at the panel's public name,
  not an internal IP.
- **Purchases** (IP changes/adds, real server orders) stay off until an
  admin enables them per account under Credentials. Set the per-account
  daily IP cap (Settings) before handing an `ip_change` token to a script.
- **Billing** refreshes hourly per account (`billing_interval_min` setting,
  minimum 5).

## Health check

`GET /api/health` answers 200 only when the database answers a `SELECT 1` —
safe as a load-balancer / watchdog probe.

## Updating

**Built-in (git checkout installs):** Settings → Panel update. It checks
GitHub daily and one click runs: `git pull` → `uv sync` → `npm ci &&
npm run build` → the process **exits with code 78**. Your supervisor
restarts it on the new code. Configure the restart:

- systemd: `Restart=always` (or `RestartForceExitStatus=78` if you restrict restarts)
- loop script: `while true; do uv run uvicorn server.main:app; [ $? -ne 78 ] && break; done`
  — or simply restart on any exit; 78 is just the documented handshake.

**Docker — self-updating container (updates from the web UI):** run the
image with the host's git checkout bind-mounted at `/repo`:

```bash
git clone https://github.com/morewebs/OmniCloud.git && cd OmniCloud
docker build -t omnicloud .
docker run -d --name omnicloud -p 8000:8000 \
  --restart unless-stopped \
  -v "$(pwd):/repo" \
  -v omnicloud-data:/data \
  -e OMNICLOUD_MASTER_KEY=<key> \
  omnicloud
```

Settings → Panel update works exactly like on a host install: apply runs
`git pull` + `uv sync` + `npm build` **inside** the container, the process
exits 78, and `--restart` reloads the new code. No docker socket is
mounted; the container never talks to the Docker daemon. First boot seeds
the bind mount with the image's prebuilt SPA; every later build happens
in-container.

Tradeoff to know: a self-updating container needs write access to its own
code tree, so it runs as root (the checkout is owned by your host user and
UID-mapping would break `git pull`). A container that can rewrite its own
code is root-in-container by design — accept this mode only on a host you
already trust with the panel (which holds your master key anyway).

**Docker — plain image (updates by rebuilding):** without the `/repo`
bind-mount the panel reports "updates by rebuilding the image" and the
Apply button is absent:

```bash
git pull
docker build -t omnicloud .
docker stop omnicloud && docker rm omnicloud
# then re-run your docker run command (same -v volume keeps the DB)
```

**Manual** (non-git installs, no node on the box):

```bash
git pull
uv sync
cd web && npm install && npm run build && cd ..
# restart the process; schema migrations run on boot
```

The app refuses to start against a **newer** schema than the build knows
(downgrades would corrupt data). Restore order: code first, then DB.

Schema v4 (IP management, billing, real orders) migrates a v3 database in
place on first boot: additive columns plus a rebuild of the `orders` table
(order history and events are kept). Take a backup first - once migrated,
an older build refuses the database.

## Docker

```bash
docker build -t omnicloud .
docker run -d -p 8000:8000 \
  -v omnicloud-data:/data \
  -e OMNICLOUD_MASTER_KEY=<key> \
  omnicloud
```

The container stores the DB at `/data/omnicloud.db` (named volume); back that
volume up the same way.

## Dockerfile

Multi-stage (node builds the SPA, python-slim runs it). Non-root user, no
docs UI, `HEALTHCHECK` hits `/api/health`.
