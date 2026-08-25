/* Runs core/richtext.js over a set of bodies and prints what it drew, as
   JSON, so tests/test_richtext.py can hold the renderer to it.
   Usage: node tools/richtext-cases.mjs

   The renderer reaches core/dom.js for esc(), which reaches the i18n
   runtime, which fetches its catalog at module load. Node's fetch has no
   file: scheme, so one is installed here rather than the module being
   restructured to suit a test: what runs below is the module the browser
   loads, import chain and all. */

import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const realFetch = globalThis.fetch;
globalThis.fetch = async (input, init) => {
  const url = input instanceof URL ? input : new URL(String(input));
  if (url.protocol !== 'file:') return realFetch(input, init);
  const body = await readFile(fileURLToPath(url), 'utf8');
  return { ok: true, status: 200, json: async () => JSON.parse(body) };
};
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
/* the i18n runtime translates the static shell on load; there is none here */
globalThis.document = { documentElement: {}, querySelectorAll: () => [] };

const { renderRich } = await import('../src/memai/webui/core/richtext.js');

const LINKS = {
  '1111111111111111': { uid: '1111111111111111', type: 'note',
                        domain: 'acme/x100', snippet: 'the cache warms on boot',
                        linked: true },
  '2222222222222222': { uid: '2222222222222222', type: 'note',
                        domain: 'acme/x100', snippet: 'the queue drains nightly',
                        linked: false },
  '3333333333333333': { uid: '3333333333333333', missing: true },
};

const NL = String.fromCharCode(10);
const FENCE = String.fromCharCode(96, 96, 96);

const CASES = {
  paragraphs: 'first line\nstill the same paragraph\n\nsecond paragraph',
  heading: '=== 1. WHAT WAS MEASURED ===\nthe drain rate is flat',
  code: 'the flag is `USE_NEW_PARSER` and the field is `F100_TOTAL`',
  code_holds_its_text: 'a `**not bold**` and a `[[1111111111111111]]` stay text',
  bold: 'the batch is **not** the bound',
  bullets: '- first point\n- second point\n- third point',
  ordered: '1. warm the cache\n2. drain the queue\n3. export the rows',
  nested: '- outer point\n  - inner point\n- back outside',
  continuation: '- a point that runs\n  onto a second line\n- the next point',
  spaced_items: ['1. first point', '', '2. second point', '', '3. third point'].join('\n'),
  spaced_items_with_body: ['1. first point', '   a line under it', '',
                           '2. second point'].join('\n'),
  list_then_paragraph: ['- a point', '', 'not a point any more'].join('\n'),
  list_starting_at_three: ['3. third', '4. fourth'].join('\n'),
  spaced_nested: ['- outer', '', '  - inner', '', '- back outside'].join('\n'),
  fenced: [FENCE + 'powershell', '$pids = Get-NetTCPConnection -LocalPort 8080',
           'foreach ($p in $pids) { }', FENCE].join(NL),
  fenced_no_language: [FENCE, 'plain lines', FENCE].join(NL),
  fenced_holds_its_markup: [FENCE + 'sql',
                            '-- a `code` span and **bold** and [[1111111111111111]]',
                            FENCE].join(NL),
  fenced_keeps_blank_lines: [FENCE + 'bash', 'one', '', 'two', FENCE].join(NL),
  fenced_unclosed: [FENCE + 'python', 'print(1)', 'print(2)'].join(NL),
  fenced_escapes: [FENCE + 'xml', '<b>&amp;</b>', FENCE].join(NL),
  text_after_fence: [FENCE + 'sh', 'ls', FENCE, '', 'back to prose'].join(NL),
  table: '| field | holds |\n|---|---:|\n| `intent` | 800 |\n| `pursuing` | 1500 |',
  table_short_row: '| a | b | c |\n|---|---|---|\n| only one |',
  pipes_without_separator: '| this | is |\n| not | a table |',
  link_live: 'see [[1111111111111111]] for the rest',
  link_unlinked: 'see [[2222222222222222]] for the rest',
  link_dead: 'see [[3333333333333333]] for the rest',
  link_by_name: 'see [[fluxo-cross-repo-memai]] for the rest',
  url: 'documented at https://example.com/x100/p200 and nowhere else',
  url_trailing_dot: 'documented at https://example.com/x100.',
  escaping: 'a <script>alert(1)</script> and a & and a "quote"',
  escaping_in_code: 'the tag is `<memory>` and the op is `a & b`',
  escaping_in_table: '| head |\n|---|\n| <b>x</b> |',
  unknown_shape: '    indented text nobody taught it\n    lined up by hand',
  empty: '',
};

const out = {};
for (const [name, body] of Object.entries(CASES)) out[name] = renderRich(body, LINKS);
process.stdout.write(JSON.stringify(out, null, 2));
