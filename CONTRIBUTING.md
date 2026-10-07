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

5. **Money is sent once.** Any provider call that buys something (an IP,
   a server) uses `request(..., retry=False)` and confirms the result on
   the provider's own view; an order left unpaid raises `PaymentRequired`,
   never success. Capabilities are an explicit set per adapter (never
   `frozenset(Capability)`), so a new capability is opt-in.

## Setup & checks

```bash
uv sync
uv run pytest            # backend (network-guarded)
cd web && npm install && npm run build   # frontend gate (tsc strict + vite)
```

The frontend typechecks under `strict`; keep it that way. Match the
surrounding code's density — ponytail rules apply (the laziest correct diff
wins, no speculative abstraction).
