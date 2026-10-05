/* The JSON API client for memai/admin.py. The application/json content type forces a preflight,
   which keeps other pages from POSTing to the loopback port (SameOriginMiddleware); keep it. */

export async function api(path, opts = {}) {
  if (opts.body !== undefined) {
    opts.method = opts.method || 'POST';
    opts.headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    opts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch { /* no body */ }
  if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
  return data;
}

/* A path segment that came from data, not from us. */
export const seg = value => encodeURIComponent(String(value ?? ''));

/* A query string from a plain object, dropping empty values. */
export const query = obj => {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(obj)) if (v !== '' && v != null) qs.set(k, String(v));
  return qs.toString();
};
