/* Runs i18n.js over a set of lookups and prints what came back, as JSON, so
   tests/test_i18n.py can hold t() to it.
   Usage: node tools/i18n-cases.mjs [locale]

   The runtime fetches its catalog from /static/i18n at module load, and
   nothing serves that path here, so a fetch reading the catalog off disk is
   installed rather than the module being restructured to suit a test: what
   runs below is the module the browser loads. Same shims as
   tools/richtext-cases.mjs. */

import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const CATALOGS = '/static/i18n/';
const CATALOG_DIR = new URL('../src/memai/webui/public/i18n/', import.meta.url);
const locale = process.argv[2] || 'en';

const realFetch = globalThis.fetch;
globalThis.fetch = async (input, init) => {
  const path = String(input);
  if (!path.startsWith(CATALOGS)) return realFetch(input, init);
  const file = new URL(path.slice(CATALOGS.length), CATALOG_DIR);
  const body = await readFile(fileURLToPath(file), 'utf8');
  return { ok: true, status: 200, json: async () => JSON.parse(body) };
};
/* the locale under test comes from storage, the same way the browser picks it */
globalThis.localStorage = { getItem: () => locale, setItem: () => {} };
globalThis.document = { documentElement: {}, querySelectorAll: () => [] };
/* core/shared.js reads the type colours out of CSS custom properties at
   module load; there is no stylesheet here and none of the cases below
   depend on a colour */
globalThis.getComputedStyle = () => ({ getPropertyValue: () => '' });

const { t } = await import('../src/memai/webui/i18n.js');
const { relLabel, relTypeTitle, REL_SUGGEST, DG_REL_SUGGEST } =
  await import('../src/memai/webui/core/shared.js');
const { dayKey, monthKey, fromKey } = await import('../src/memai/webui/core/dom.js');

/* [name, key, vars] -- one lookup per case */
const CASES = [
  ['plural-one', 'op.what.archive', { n: 1 }],
  ['plural-many', 'op.what.archive', { n: 3 }],
  ['plural-zero', 'op.what.archive', { n: 0 }],
  /* fmtInt puts a separator in before t() ever sees the value, so the branch
     has to read the number rather than the text it interpolates */
  ['plural-formatted', 'op.what.archive', { n: '1' }],
  ['plural-thousand', 'op.what.archive', { n: 1024 }],
  ['plural-missing-var', 'op.what.archive', {}],
  /* two independently counted words in one sentence */
  ['plural-two-counts-one', 'op.what.retag', { n: 1, terms: 1 }],
  ['plural-two-counts-mixed', 'op.what.retag', { n: 1, terms: 4 }],
  ['plural-two-counts-many', 'op.what.retag', { n: 3, terms: 9 }],
  /* a value carrying braces must not be re-scanned as a plural form */
  ['plural-not-in-values', 'op.what.redomain', { n: 1, to: 'acme/{x}' }],
  ['no-vars-leaves-form', 'op.what.archive', null],
  ['missing-key', 'op.what.nosuchkind', { n: 2 }],
  ['pt-plural-one', 'op.what.merge', { n: 1 }],
  ['pt-plural-many', 'op.what.merge', { n: 2 }],
  ['sentence-link', 'op.what.link', { n: 1, rel: 'relates_to' }],
  ['sentence-distill', 'op.what.distill', { n: 1, sources: 3 }],
  ['groups-aside', 'op.grp.aside', { n: 18, g: 1 }],
  ['hint-some-one', 'op.vf.hintSome', { n: 1 }],
  ['hint-some-many', 'op.vf.hintSome', { n: 5 }],
];

const out = {};
for (const [name, key, vars] of CASES) out[name] = t(key, vars);

/* relLabel is not a lookup: it decides whether the catalog HAS a name for a
   type and falls back to the stored string when it does not. */
out['rel-known'] = relLabel('supersedes');
out['rel-known-two-words'] = relLabel('relates_to');
out['rel-diagram-only'] = relLabel('explains');
out['rel-custom'] = relLabel('blocks_the_release');
out['rel-empty'] = relLabel('');
out['rel-padded'] = relLabel('  supersedes  ');
out['rel-title-known'] = relTypeTitle('supersedes');
out['rel-title-custom'] = relTypeTitle('blocks_the_release');
out['rel-every-offered'] = [...new Set([...REL_SUGGEST, ...DG_REL_SUGGEST])]
  .map(r => `${r}=${relLabel(r)}`).join(' · ');

/* The calendar's whole correctness rests on these two: a timestamp is UTC
   and a day is the reader's own. */
const at = (y, m, d, hh, mm) => new Date(y, m - 1, d, hh, mm);
out['day-key'] = dayKey(at(2026, 9, 9, 14, 0));
out['day-key-pads'] = dayKey(at(2026, 1, 5, 0, 30));
/* late evening local: whatever UTC calls it, the reader lived this day */
out['day-key-late'] = dayKey(at(2026, 9, 8, 23, 40));
out['day-key-early'] = dayKey(at(2026, 9, 9, 0, 20));
out['month-key'] = monthKey(at(2026, 9, 9, 14, 0));
/* the inverse, and why it is not new Date(key): that parses as UTC */
out['from-key'] = dayKey(fromKey('2026-09-09'));
out['from-key-naive-utc'] = dayKey(new Date('2026-09-09'));
out['from-key-roundtrip'] = dayKey(fromKey(dayKey(at(2026, 3, 1, 2, 0))));
out['from-key-month'] = String(fromKey('2026-09-09').getMonth());

console.log(JSON.stringify(out, null, 2));
