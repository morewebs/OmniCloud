// fetch wrapper: sets X-Requested-With on mutations (CSRF), JSON everywhere,
// throws Error(detail) on non-2xx so callers render provider-safe messages.

export async function api<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const method = (init?.method ?? 'GET').toUpperCase();
  if (method !== 'GET') headers['X-Requested-With'] = 'XMLHttpRequest';
  const r = await fetch(path, { ...init, headers: { ...headers, ...init?.headers as object } });
  if (!r.ok) {
    let detail = `${r.status}`;
    try { const j = await r.json(); if (j.detail) detail = String(j.detail); } catch { /* keep */ }
    const err = new Error(detail) as Error & { status?: number };
    err.status = r.status;
    throw err;
  }
  return r.json() as Promise<T>;
}

export function post(path: string, body?: unknown) {
  return api(path, { method: 'POST', body: JSON.stringify(body ?? {}) });
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
export function subscribeStream(onEvent: (event: string) => void): () => void {
  const es = new EventSource('/api/stream');
  es.onmessage = (m) => {
    try {
      const parsed = JSON.parse(m.data) as { event: string };
      onEvent(parsed.event);
    } catch { /* ignore malformed */ }
  };
  return () => es.close();
}

export function fmtBytes(n: number | null | undefined): string {
  if (n == null) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let v = n, i = 0;
  while (v >= 1000 && i < units.length - 1) { v /= 1000; i++; }
  return `${v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2)} ${units[i]}`;
}

export function fmtMoney(m: { amount: string; currency: string } | null | undefined): string {
  if (!m) return '';
  return `€${Number(m.amount).toFixed(2)}`;
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
