# Security Policy

## Reporting a vulnerability

Email the maintainer directly (see the GitHub profile on the repo) or open a
**private** security advisory: repo page → Security → Advisories. Please do
not open a public issue for anything exploitable.

You will get an acknowledgment within 72 hours. We target disclosure after a
fix ships; credited reports welcome, coordinated disclosure expected.

## Scope

OmniCloud is a self-hosted panel that stores **provider API tokens** — treat
an install as critical infrastructure:

- The database (`omnicloud.db`) holds Fernet-encrypted tokens. It is only as
  safe as the master key (`OMNICLOUD_MASTER_KEY`) and the disk it sits on.
  See [DEPLOY.md](DEPLOY.md) — back up key and DB together.
- Tokens are never returned by any API (only the last 4 characters), never
  logged, never rendered by the UI. Any finding that breaks this is
  high-severity.
- Auth: scrypt-hashed passwords, opaque session cookies (HttpOnly,
  SameSite=Strict, Secure by default), per-username lockout, CSRF via the
  required `X-Requested-With` header on every non-GET `/api` request.

## Hardening checklist for operators

1. Serve behind TLS only (`OMNICLOUD_COOKIE_SECURE=1`, the default).
2. Keep `OMNICLOUD_MASTER_KEY` out of the repo and out of env dumps.
3. Restrict network exposure — the panel needs no inbound port beyond your
   reverse proxy.
4. Update promptly (Settings → Panel update, or see DEPLOY.md).
