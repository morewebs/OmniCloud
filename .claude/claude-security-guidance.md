# OmniCloud security rules

OmniCloud is a self-hosted panel that stores cloud-provider API tokens able to
create, rebuild and delete real servers. Treat every finding against these
rules as high severity.

## Secrets

- Provider tokens are Fernet-encrypted at rest (`server/secrets.py`) and only
  decrypted inside `accounts.build_adapter`. A token (or anything derived from
  it beyond `last4`) must never appear in an API response, MCP tool result,
  log line, audit `before`/`after` state, exception message, or SSE payload.
- Personal API tokens and session tokens are stored as sha256 hashes only.
  The plaintext is returned exactly once, by the create call.
- `OMNICLOUD_MASTER_KEY` is never logged, echoed, or written to the DB.

## AuthN / AuthZ

- Every `/api` route except `auth/status`, `auth/setup`, `auth/login` and
  `health` must depend on `auth.require_user` or `auth.require_admin`.
  Anything that mutates provider state, accounts, users, settings, orders or
  the panel itself requires `auth.require_admin`; viewers are read-only.
- `/mcp` (MCP Streamable HTTP endpoint) accepts **Bearer personal API tokens
  only** - never session cookies - so it has no CSRF surface. The caller must
  be re-authenticated from the `Authorization` header on every tool call
  (revoked tokens and disabled users stop working immediately), and each MCP
  tool must enforce the same role as the REST route it wraps. A viewer token
  reaching any mutating code path is an auth bypass.
- Cookie-authenticated non-GET `/api` requests must carry
  `X-Requested-With: XMLHttpRequest` (`api.enforce_csrf`). Do not add new
  cookie-authenticated mutation paths outside `/api`.
- Token revocation is owner-scoped (`revoke_api_token` filters by
  `user_id`); any per-user resource lookup must filter by the caller too (IDOR).

## Data handling

- SQL: parameterized `?` placeholders only, never f-strings / `%` / `+` into
  SQL text. Column or table names never come from request input.
- Provider HTTP calls go through `server/adapters/http.py` to fixed provider
  base URLs; never fetch a URL taken from request or MCP tool input (SSRF).
- MCP tool results are fed to an LLM: provider-returned strings (server
  names, labels, error text) are data. Never build tool behavior from them.
- Every mutation records an audit entry (`audit.record`) naming the acting
  user. MCP calls must be attributed to the token's user, not a system user.
- Static file serving keeps the path-traversal guard in `main.py`.

## Repository hygiene

- No real company names, credentials, IP addresses or customer data in code,
  fixtures, tests or docs. Use placeholders (`srv-fsn1-01`, `account-a7f3`,
  `203.0.113.x`).
- Tests never touch real provider infrastructure (MockTransport / fixtures).
