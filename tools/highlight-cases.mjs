/* Reports what the dashboard's highlighter can do, as JSON, so
   tests/test_richtext.py can hold it to working: the whole catalogue
   highlight.js ships, the alias table read off those grammars, and one
   coloured snippet per language there is a snippet for.

   Usage: node tools/highlight-cases.mjs */

import { aliases, languages } from './hljs-catalog.mjs';

const hljs = (await import('highlight.js/lib/core')).default;
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
  rust: 'fn warm(cache: &mut Cache) -> usize {\n    cache.fill(1) // the first request\n}',
  go: 'func Warm(c *Cache) int {\n\treturn c.Fill(1) // the first request\n}',
  csharp: 'public int Warm(Cache c) => c.Fill(1); // the first request',
};

const catalog = await languages();
const out = { catalog, aliases: await aliases(catalog), coloured: {}, failed: [] };

for (const language of Object.keys(SNIPPETS).sort()) {
  try {
    if (!catalog.includes(language)) { out.failed.push(`${language}: not shipped`); continue; }
    const grammar = (await import(`highlight.js/lib/languages/${language}`)).default;
    hljs.registerLanguage(language, grammar);
    const snippet = SNIPPETS[language];
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
