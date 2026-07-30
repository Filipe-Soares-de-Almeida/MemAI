/* The one door to the JSON API in memai/admin.py.

   Every path is built here rather than by callers, so uids go through
   encodeURIComponent in one place: a uid is 16 hex chars today, but a
   path segment interpolated raw is a trap waiting for the day it is not.

   The admin server requires application/json on a body (see its
   SameOriginMiddleware) -- that content type is what forces a browser
   preflight, which is what keeps another page in the browser from
   POSTing to the loopback port. Do not "simplify" it away. */

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
