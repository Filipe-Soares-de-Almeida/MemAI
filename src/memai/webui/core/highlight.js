/* Syntax highlighting for the fenced code blocks a memory's body carries.

   The engine is highlight.js, an npm dependency, imported whole: every
   language it ships is registered, so a body written in any of the 193
   colours without a list to keep anywhere. The engine resolves each
   grammar's own aliases too, which is how a fence tagged `rs`, `c#` or
   `golang` finds its language.

   The import is dynamic, so the engine arrives with the first highlighted
   block and a page with no code block never loads it. It is one chunk, read
   from loopback.

   A language the engine does not know leaves the block plain monospace,
   which is also what happens when the load fails. The text is already on
   screen by then, escaped, so the worst case is colourless -- never empty,
   never mangled. */

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

/* Colour every code block under `root` that names a language the engine has.

   Idempotent: a block is marked once it has been through, so re-rendering a
   record does not highlight the same text twice. Blocks are read before the
   await, so a dialog that closes mid-load simply updates nothing. */
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
