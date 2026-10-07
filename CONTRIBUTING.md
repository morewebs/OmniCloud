# Contributing

Issues and PRs welcome. The short version:

1. **Provider facts are cited.** Anything a provider adapter claims
   (endpoint shape, price format, traffic counting) must match
   `docs/provider-truth.md`, which links the official source. If you add a
   provider fact, add the citation. If your change contradicts the doc,
   update the doc (with the source) in the same PR.
2. **Data honesty is the product.** A value the provider does not expose
   renders "not exposed" or "—" — never zero, never guessed. See
   `design.md` §6. Tests assert this; they will fail you.
3. **No real infrastructure, ever, in tests.** All adapter tests run on
   recorded fixture JSON (`server/fixtures/`) via `httpx.MockTransport`. A
   test that can touch a real provider is a bug — the suite has a network
   guard that will catch it.
4. **No secrets in the repo.** No real company names, credentials, IPs, or
   customer data anywhere — code, fixtures, screenshots, or docs. Example
   data uses placeholder identifiers only (`srv-fsn1-01`, `203.0.113.x`).
5. **The MCP server mirrors the REST API.**
   - **New or changed `/api` route:** add or update its tool in
     `server/mcp_server.py`.
   - **How a tool works:** it calls the route function through `_call`,
     never a copy of the route's logic, so the route's
     `Depends(require_user / require_admin)` stays the only role check.
     Server actions are the one exception: they go through
     `_server_action`, which runs the same `api.check_action` pre-flight.
   - **Docs and tests:** list the tool in `docs/mcp.md` and cover it in
     `tests/test_mcp.py`. Browser-only routes (login, setup, the SSE
     stream) are the exception; `docs/mcp.md` says which.

## Setup & checks

```bash
uv sync
uv run pytest            # backend (network-guarded)
cd web && npm install && npm run build   # frontend gate (tsc strict + vite)
```

The frontend typechecks under `strict`; keep it that way. Match the
surrounding code's density — ponytail rules apply (the laziest correct diff
wins, no speculative abstraction).
