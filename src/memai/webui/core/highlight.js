/* Syntax highlighting for fenced code blocks, with highlight.js loaded whole on the first block.
   An unknown language or a failed load leaves the escaped text plain, never empty. */

/* Names no grammar answers to, plus `shell`: highlight.js reads that as a
   terminal session, and a fence tagged that way here is a script. */
const OVERRIDES = {
  shell: 'bash',
  node: 'javascript',
  objectpascal: 'delphi',
  tsql: 'sql', mssql: 'sql', plsql: 'sql', psql: 'sql',
  cfg: 'ini', conf: 'ini',
};

let enginePromise = null;

const engine = () => {
  if (!enginePromise) {
    enginePromise = import('highlight.js')
      .then(m => {
        const hljs = m.default;
        /* the body is inserted as text, so a class this does not know about
           cannot be smuggled in through it; the warning is noise here */
        hljs.configure({ ignoreUnescapedHTML: true });
        return hljs;
      })
      .catch(() => null);
  }
  return enginePromise;
};

/* Colour every code block under `root` whose language the engine knows. Idempotent; blocks are
   read before the await, so a dialog closed mid-load updates nothing. */
export async function highlightIn(root) {
  const blocks = [...root.querySelectorAll('code[data-lang]:not([data-hl])')];
  if (!blocks.length) return;
  const hljs = await engine();
  if (!hljs) return;
  for (const block of blocks) {
    const tag = String(block.dataset.lang || '').trim().toLowerCase();
    const language = OVERRIDES[tag] || tag;
    block.dataset.hl = 'done';
    if (!language || !hljs.getLanguage(language)) continue;
    try {
      block.innerHTML = hljs.highlight(block.textContent, { language }).value;
    } catch {
      /* a grammar that throws on this text leaves it as it was */
    }
  }
}
