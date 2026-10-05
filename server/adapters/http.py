"""Shared provider HTTP client: retry/backoff on 429, pagination helpers.

Auth is injected as a callable so OVH's signed scheme (method+path+body
hashing) slots in later without touching sync.py or api.py. Headers are never
logged and never included in raised error messages.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

import httpx

from .base import AdapterError

# 429: honor Retry-After, else this capped ladder; max 4 tries total.
BACKOFF_LADDER = (1.0, 2.0, 4.0)
MAX_TRIES = 4
RETRIES_5XX = 2
TIMEOUT_S = 20.0


class ProviderHttpClient:
    def __init__(self, base_url: str, auth: Callable[[httpx.Request], None],
                 transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=TIMEOUT_S,
            transport=transport,  # None = real network; MockTransport in tests
            headers={"User-Agent": "omnicloud/0.1"},
        )
        self._auth = auth

    async def request(self, method: str, path: str, **kw) -> httpx.Response:
        """Send with auth + retry/backoff. Raises AdapterError on final failure
        (message contains status + path only, never headers or token)."""
        tries_5xx = 0
        for attempt in range(MAX_TRIES):
            req = self._client.build_request(method, path, **kw)
            self._auth(req)
            resp = await self._client.send(req)
            if resp.status_code == 429 and attempt < MAX_TRIES - 1:
                await asyncio.sleep(self._retry_after(resp, attempt))
                continue
            if 500 <= resp.status_code < 600 and tries_5xx < RETRIES_5XX:
                tries_5xx += 1
                await asyncio.sleep(self._retry_after(resp, attempt))
                continue
            return resp
        raise AdapterError(f"{method} {path}: rate limit persisted after retries")

    async def get_json(self, path: str, params: dict | None = None) -> Any:
        r = await self.request("GET", path, params=params)
        self._raise_for_status(r)
        return r.json()

    async def post_json(self, path: str, body: dict | None = None) -> httpx.Response:
        r = await self.request("POST", path, json=body)
        self._raise_for_status(r)
        return r

    async def put_json(self, path: str, body: dict | None = None) -> httpx.Response:
        r = await self.request("PUT", path, json=body)
        self._raise_for_status(r)
        return r

    async def delete(self, path: str) -> httpx.Response:
        r = await self.request("DELETE", path)
        self._raise_for_status(r)
        return r

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _retry_after(resp: httpx.Response, attempt: int) -> float:
        # Retry-After (seconds) if present, else Hetzner's RateLimit-Reset
        # (a UNIX timestamp of full recovery) - verified header names,
        # see docs/provider-truth.md. LeaseWeb documents neither; the
        # capped ladder covers it.
        if "Retry-After" in resp.headers:
            try:
                return max(0.0, float(resp.headers["Retry-After"]))
            except ValueError:
                pass
        reset = resp.headers.get("RateLimit-Reset")
        if reset:
            try:
                import time
                wait = float(reset) - time.time()
                if 0 < wait <= 60:  # sane cap; huge waits fall to the ladder
                    return wait + 1.0
            except ValueError:
                pass
        return BACKOFF_LADDER[min(attempt, len(BACKOFF_LADDER) - 1)]

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            # Body can carry a provider error message; truncate for sanity.
            snippet = resp.text[:200].replace("\n", " ")
            raise AdapterError(
                f"{resp.request.method} {resp.request.url.path}: "
                f"{resp.status_code} - {snippet}",
                status_code=resp.status_code,
            )


HETZNER_PAGE_CAP = 25


def iter_pages_hetzner(path: str, per_page: int = 50) -> list[tuple[str, dict]]:
    """Hetzner paginates with page= + meta.pagination.last_page. The consumer
    breaks on last_page; if it ever exhausts this list, raise - a truncated
    fleet is silent data loss, never a success (data honesty)."""
    return [(path, {"page": p, "per_page": per_page})
            for p in range(1, HETZNER_PAGE_CAP + 1)]
