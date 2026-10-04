# Deploying OmniCloud

Single-process by design (uvicorn, one worker): the SSE hub, sync loops, and
login rate-limiter are in-process state. Run one process per install; it
comfortably serves a small ops team over a 200+ server fleet.

## Quick production run

```bash
uv sync
export OMNICLOUD_MASTER_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
cd web && npm install && npm run build && cd ..   # SPA into server/static/
uv run uvicorn server.main:app --host 0.0.0.0 --port 8000
```

First visit shows the one-time setup screen (creates the first admin; it
closes forever after).

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `OMNICLOUD_MASTER_KEY` | *(empty)* | Fernet key encrypting provider API tokens. Without it the app runs but credential operations return 503. **Back this up separately from the DB** — losing it loses every stored credential. |
| `OMNICLOUD_DB` | `./omnicloud.db` | SQLite path (WAL mode). Put it on a real disk, not a container overlay you never back up. |
| `OMNICLOUD_SESSION_TTL_DAYS` | `30` | Session lifetime. |
| `OMNICLOUD_SYNC_INTERVAL_MIN` | `5` | Default fleet sync interval (per-account override in Settings). |
| `OMNICLOUD_COOKIE_SECURE` | `1` | Session cookie `Secure` flag. Set `0` **only** for plain-HTTP local dev; behind TLS keep `1`. |

## Backups

`omnicloud.db` holds Fernet-encrypted provider tokens — it *is* the install.
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
location / { proxy_pass http://127.0.0.1:8000; }
```

`proxy_read_timeout 960s` matters: a rebuild/delete waits for the provider's
own confirmation (up to 900s). A proxy that cuts at 60s returns a 502 to the
operator while the action still completes server-side.

## Health check

`GET /api/health` answers 200 only when the database answers a `SELECT 1` —
safe as a load-balancer / watchdog probe.

## Updating

**Built-in (git checkout installs):** Settings → Panel update. It checks
GitHub daily and one click runs: `git pull` → `uv sync` → `npm install &&
npm run build` → the process **exits with code 78**. Your supervisor
restarts it on the new code. Configure the restart:

- systemd: `Restart=always` (or `RestartForceExitStatus=78` if you restrict restarts)
- loop script: `while true; do uv run uvicorn server.main:app; [ $? -ne 78 ] && break; done`
  — or simply restart on any exit; 78 is just the documented handshake.

**Docker installs update by rebuilding the image** — the container has no
git or node, so the built-in apply is not for Docker (its update panel will
say so):

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
# restart the process; schema changes are additive and applied on boot
```

The app refuses to start against a **newer** schema than the build knows
(downgrades would corrupt data). Restore order: code first, then DB.

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
