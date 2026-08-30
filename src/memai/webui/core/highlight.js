/* Syntax highlighting for the fenced code blocks a memory's body carries.

   The engine is highlight.js, an npm dependency. Every language it ships is
   available: the grammar map and the alias table are generated at build time
   from the installed package (see the hljs-catalog plugin in
   vite.config.js), so a body written in any of them colours without anything
   being added here.

   Nothing is loaded until a block asks for it. The catalog and the engine
   come in on the first highlighted block and each grammar on the first block
   written in that language, every one its own lazy chunk, so a page with no
   code block loads none of it.

   A language the engine does not know leaves the block plain monospace,
   which is also what happens when a load fails. The text is already on
   screen by then, escaped, so the worst case is colourless -- never empty,
   never mangled. */

/* Names the grammars do not claim for themselves, plus the ones this
   dashboard routes elsewhere on purpose: highlight.js reads `shell` as a
   terminal session, and a fence tagged that way here is a script. */
const OVERRIDES = {
  shell: 'bash',
  node: 'javascript',
  objectpascal: 'delphi',
  tsql: 'sql', mssql: 'sql', plsql: 'sql', psql: 'sql',
  cfg: 'ini', conf: 'ini',
};

let catalogPromise = null;
let enginePromise = null;
const grammars = new Map();   // language -> Promise<boolean>, false once it has failed

/* the generated GRAMMARS/ALIASES, kept out of the eager bundle */
const catalog = () => (catalogPromise ??= import('virtual:hljs-catalog')
  .catch(() => ({ GRAMMARS: {}, ALIASES: {} })));

const engine = () => {
  if (!enginePromise) {
    enginePromise = import('highlight.js/lib/core')
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

const canonical = (table, name) => {
  const key = String(name || '').trim().toLowerCase();
  return OVERRIDES[key] || table[key] || key;
};

function grammar(hljs, importers, language) {
  if (!grammars.has(language)) {
    const load = importers[language];
    grammars.set(language, !load ? Promise.resolve(false)
      : load()
        .then(m => { hljs.registerLanguage(language, m.default); return true; })
        .catch(() => false));
  }
  return grammars.get(language);
}

/* Colour every code block under `root` that names a language the engine has.

   Idempotent: a block is marked once it has been through, so re-rendering a
   record does not highlight the same text twice. Blocks are read before the
   first await, so a dialog that closes mid-load simply updates nothing. */
export async function highlightIn(root) {
  const blocks = [...root.querySelectorAll('code[data-lang]:not([data-hl])')];
  if (!blocks.length) return;
  const [{ GRAMMARS, ALIASES }, hljs] = await Promise.all([catalog(), engine()]);
  if (!hljs) return;
  await Promise.all(blocks.map(async block => {
    const language = canonical(ALIASES, block.dataset.lang);
    block.dataset.hl = 'done';
    if (!language || !(await grammar(hljs, GRAMMARS, language))) return;
    try {
      block.innerHTML = hljs.highlight(block.textContent, { language }).value;
    } catch {
      /* a grammar that throws on this text leaves it as it was */
    }
  }));
}
