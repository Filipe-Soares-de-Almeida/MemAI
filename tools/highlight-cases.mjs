/* Loads the vendored highlight.js the way the dashboard does and colours one
   snippet per language, so tests/test_richtext.py can hold the vendored tree
   to actually working: a grammar that goes missing on an update stops being
   a silently colourless block and becomes a failing test.

   Usage: node tools/highlight-cases.mjs */

import { readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const HERE = new URL('../src/memai/webui/vendor/highlight/', import.meta.url);
const hljs = (await import(new URL('core.min.js', HERE))).default;
hljs.configure({ ignoreUnescapedHTML: true });

const SNIPPETS = {
  powershell: 'Get-NetTCPConnection -LocalPort 8080 | Stop-Process -Force  # by port',
  bash: 'grep -rn "x100" src/ | head -5   # find the module',
  sql: "SELECT id FROM orders WHERE total > 100 -- only the big ones",
  python: 'def warm(cache):\n    return cache.fill(1)  # the first request',
  javascript: 'const total = rows.filter(r => r.ok).length; // how many passed',
  delphi: 'procedure Warm(const Cache: TCache);\nbegin\n  Cache.Fill(1); // first\nend;',
  json: '{"domain": "acme/x100", "total": 42}',
  xml: '<config><flag name="USE_NEW_PARSER">true</flag></config>',
  ini: '[acme]\nflag = USE_NEW_PARSER   ; a comment',
  yaml: 'domain: acme/x100\ntotal: 42   # a comment',
  diff: '--- a\n+++ b\n-gone\n+arrived',
};

const shipped = (await readdir(new URL('languages/', HERE)))
  .filter(f => f.endsWith('.min.js')).map(f => f.replace('.min.js', '')).sort();

const out = { shipped, coloured: {}, failed: [] };
for (const language of shipped) {
  try {
    const grammar = (await import(new URL(`languages/${language}.min.js`, HERE))).default;
    hljs.registerLanguage(language, grammar);
    const snippet = SNIPPETS[language];
    if (snippet === undefined) { out.failed.push(`${language}: no snippet`); continue; }
    const html = hljs.highlight(snippet, { language }).value;
    out.coloured[language] = {
      /* what the theme in admin.css has to have a rule for */
      scopes: [...new Set([...html.matchAll(/class="(hljs-[\w-]+)"/g)].map(m => m[1]))].sort(),
      /* the text must survive being coloured, character for character */
      intact: html.replace(/<[^>]+>/g, '')
        .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
        .replace(/&quot;/g, '"').replace(/&#x27;/g, "'") === snippet,
    };
  } catch (e) {
    out.failed.push(`${language}: ${e.message}`);
  }
}
process.stdout.write(JSON.stringify(out, null, 2));
