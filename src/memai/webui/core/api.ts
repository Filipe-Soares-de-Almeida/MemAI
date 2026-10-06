/* The JSON API client for memai/admin.py. The application/json content type forces a preflight,
   which keeps other pages from POSTing to the loopback port (SameOriginMiddleware); keep it. */

export interface ApiOptions {
  method?: string;
  headers?: Record<string, string>;
  body?: unknown;
}

export async function api<T = unknown>(path: string, opts: ApiOptions = {}): Promise<T> {
  const init: RequestInit = { method: opts.method, headers: opts.headers };
  if (opts.body !== undefined) {
    init.method = opts.method || 'POST';
    init.headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, init);
  let data: { error?: string } | null = null;
  try { data = await res.json(); } catch { /* no body */ }
  if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
  return data as T;
}

/* A path segment that came from data, not from us. */
export const seg = (value: unknown): string => encodeURIComponent(String(value ?? ''));

/* A query string from a plain object, dropping empty values. */
export const query = (obj: Record<string, unknown>): string => {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(obj)) if (v !== '' && v != null) qs.set(k, String(v));
  return qs.toString();
};
