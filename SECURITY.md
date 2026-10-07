# Security Policy

## Reporting a vulnerability

Email the maintainer directly (see the GitHub profile on the repo) or open a
**private** security advisory: repo page → Security → Advisories. Please do
not open a public issue for anything exploitable.

You will get an acknowledgment within 72 hours. We target disclosure after a
fix ships; credited reports welcome, coordinated disclosure expected.

## Scope

OmniCloud is a self-hosted panel that stores **provider credentials** - API
tokens, and for providers without tokens (Gcore Hosting, Tube-hosting) the
account login itself, which cannot be scoped down. It can also **spend money**
at providers. Treat an install as critical infrastructure:

- The database (`omnicloud.db`) holds Fernet-encrypted tokens. It is only as
  safe as the master key (`OMNICLOUD_MASTER_KEY`) and the disk it sits on.
  See [DEPLOY.md](DEPLOY.md) — back up key and DB together.
- Tokens are never returned by any API (only the last 4 characters), never
  logged, never rendered by the UI. Any finding that breaks this is
  high-severity.
- Auth: scrypt-hashed passwords, opaque session cookies (HttpOnly,
  SameSite=Strict, Secure by default), per-username lockout, CSRF via the
  required `X-Requested-With` header on every non-GET `/api` request.
- **Personal API tokens** (0.3.0): sha256-hashed at rest, plaintext shown
  exactly once at creation, bearer-authenticated, carry the creator's role
  (a viewer's token cannot mutate), revocable at any time. Bearer requests
  skip the CSRF header check — no cookie is attached, so there is nothing
  to forge. A leaked token is high-severity: revoke it in Settings → API
  tokens.
- **Token scopes**: `full` (the creator's role) or `ip_change`, which reaches
  only `GET /api/ips/{ip}`, `POST /api/ips/{ip}/change`, `GET
  /api/actions/{id}` and `GET /api/auth/me` - the token to hand a remote
  rotation script. Any route reachable by an `ip_change` token beyond those
  is a high-severity finding.
- **Purchases**: nothing is bought unless an admin enabled purchases on that
  account (audited). IP acquisitions are capped per account per 24 h, one IP
  operation per server at a time, and purchase requests to providers are
  sent exactly once (never retried - a retry could buy twice). Panel
  passwords are POSTed to the provider, never placed in a URL.

## Hardening checklist for operators

1. Serve behind TLS only (`OMNICLOUD_COOKIE_SECURE=1`, the default).
2. Keep `OMNICLOUD_MASTER_KEY` out of the repo and out of env dumps.
3. Give scripts `ip_change` tokens, not full ones, and keep purchases off
   on accounts that don't need them.
4. Restrict network exposure — the panel needs no inbound port beyond your
   reverse proxy.
5. Update promptly (Settings → Panel update, or see DEPLOY.md).
