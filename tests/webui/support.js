/* What the dashboard modules reach outside themselves: fetch, answered from the
   locale catalogs on disk and from a per-test API handler. */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

/* happy-dom replaces the global URL, so the path is built from node:path */
const I18N = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'src', 'memai', 'webui', 'public', 'i18n');

let handler = null;
export const calls = [];

/* `fn(path, {method, body})` answers an API call; a thrown Error becomes a 500. */
export function serveApi(fn) {
  handler = fn;
  calls.length = 0;
}

const reply = (status, data) => new Response(JSON.stringify(data), {
  status, headers: { 'Content-Type': 'application/json' },
});

export async function fakeFetch(path, opts = {}) {
  const url = String(path);
  const locale = url.match(/^\/static\/i18n\/([\w-]+)\.json$/);
  if (locale) return reply(200, JSON.parse(readFileSync(join(I18N, `${locale[1]}.json`), 'utf8')));
  const call = { path: url, method: opts.method || 'GET',
                 body: opts.body === undefined ? undefined : JSON.parse(opts.body) };
  calls.push(call);
  if (!handler) return reply(404, { error: `no handler for ${url}` });
  try {
    return reply(200, await handler(call.path, call));
  } catch (err) {
    return reply(500, { error: err.message });
  }
}

/* The catalog a test reads its expected strings from. */
export const catalog = code =>
  JSON.parse(readFileSync(join(I18N, `${code}.json`), 'utf8')).strings;
