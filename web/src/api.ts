// fetch wrapper: sets X-Requested-With on mutations (CSRF), JSON everywhere,
// throws Error(human-readable) on failure so callers never render raw fetch
// internals ("Unexpected end of JSON input", "TypeError: Failed to fetch").

const TIMEOUT_MS = 30_000;

const STATUS_TEXT: Record<number, string> = {
  400: 'Bad request',
  401: 'Session expired — sign in again',
  403: 'You do not have permission for this action',
  404: 'Not found',
  409: 'Conflict',
  429: 'Rate limited — try again in a moment',
};

export async function api<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const method = (init?.method ?? 'GET').toUpperCase();
  if (method !== 'GET') headers['X-Requested-With'] = 'XMLHttpRequest';
  let r: Response;
  try {
    r = await fetch(path, {
      ...init,
      headers: { ...headers, ...init?.headers as object },
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === 'TimeoutError')
      throw new Error('Timed out — the server did not answer in 30s');
    throw new Error('Network error — check your connection and retry');
  }
  if (!r.ok) {
    let detail = STATUS_TEXT[r.status] ?? (r.status >= 500
      ? `Server error (${r.status})` : `Request failed (${r.status})`);
    try {
      const j = await r.json();
      // FastAPI {detail: "..."} — but 422 validation errors carry an ARRAY;
      // only a string detail replaces the human text
      if (typeof j?.detail === 'string') detail = j.detail;
    } catch { /* non-JSON body — keep the status text */ }
    // Session expired mid-use: bounce to the login page once, globally -
    // otherwise the operator is stranded on a dead dashboard.
    if (r.status === 401 && !path.startsWith('/api/auth/')) {
      window.location.reload();
    }
    const err = new Error(detail) as Error & { status?: number };
    err.status = r.status;
    throw err;
  }
  if (r.status === 204) return undefined as T;
  return r.json() as Promise<T>;
}

export function post<T = unknown>(path: string, body?: unknown) {
  return api<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) });
}
export function patch(path: string, body: unknown) {
  return api(path, { method: 'PATCH', body: JSON.stringify(body) });
}
export function put(path: string, body: unknown) {
  return api(path, { method: 'PUT', body: JSON.stringify(body) });
}
export function del(path: string) {
  return api(path, { method: 'DELETE' });
}

// SSE -> query invalidation. EventSource reconnects natively; each event just
// invalidates the relevant query so refetch happens through the normal cache.
// onDisconnect/onReconnect let the shell surface a live-updates indicator.
export function subscribeStream(onEvent: (event: string) => void,
                                onDisconnect?: () => void,
                                onReconnect?: () => void): () => void {
  const es = new EventSource('/api/stream');
  let wasDown = false;
  es.onmessage = (m) => {
    try {
      const parsed = JSON.parse(m.data) as { event: string };
      onEvent(parsed.event);
    } catch { /* ignore malformed */ }
  };
  es.onopen = () => {
    if (wasDown && onReconnect) onReconnect();
    wasDown = false;
  };
  es.onerror = () => { wasDown = true; if (onDisconnect) onDisconnect(); };
  return () => es.close();
}

export function fmtBytes(n: number | null | undefined): string {
  if (n == null) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let v = n, i = 0;
  while (v >= 1000 && i < units.length - 1) { v /= 1000; i++; }
  return `${v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2)} ${units[i]}`;
}

// currency-aware: a USD price renders as $, never a hardcoded € prefix.
export function fmtMoney(m: { amount: string; currency: string } | null | undefined): string {
  if (!m) return '';
  return new Intl.NumberFormat('en', { style: 'currency', currency: m.currency })
    .format(Number(m.amount));
}

// relative time ("3m ago") — the form operators scan for staleness.
export function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return '';
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return 'just now';
  const min = Math.floor(ms / 60_000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min}m ago`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}d ago`;
  return new Date(iso).toLocaleDateString();
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '';
  return new Date(iso).toLocaleString([], {
    hour: '2-digit', minute: '2-digit', day: 'numeric', month: 'short',
  });
}
