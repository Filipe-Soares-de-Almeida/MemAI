/* Syntax highlighting for the fenced code blocks a memory's body carries.

   The engine is highlight.js, vendored under webui/vendor/highlight (see the
   README there). Nothing is loaded until a block asks for it: the core comes
   in on the first highlighted block, each grammar on the first block written
   in that language, and a page with no code block loads neither.

   A language with no grammar file leaves the block plain monospace, which is
   also what happens when a load fails. The text is already on screen by
   then, escaped, so the worst case is colourless -- never empty, never
   mangled.

   Adding a language means dropping its file into vendor/highlight/languages
   and, if it goes by more than one name, adding a line to ALIASES. */

const ALIASES = {
  sh: 'bash', shell: 'bash', zsh: 'bash',
  ps1: 'powershell', pwsh: 'powershell',
  js: 'javascript', mjs: 'javascript', node: 'javascript',
  pas: 'delphi', pascal: 'delphi', objectpascal: 'delphi',
  tsql: 'sql', mssql: 'sql', plsql: 'sql', postgres: 'sql', psql: 'sql',
  py: 'python',
  yml: 'yaml',
  html: 'xml', svg: 'xml',
  cfg: 'ini', conf: 'ini', toml: 'ini',
  patch: 'diff',
};

export const canonicalLanguage = name => {
  const key = String(name || '').trim().toLowerCase();
  return ALIASES[key] || key;
};

let enginePromise = null;
const grammars = new Map();   // language -> Promise<boolean>, false once it has failed

const engine = () => {
  if (!enginePromise) {
    enginePromise = import('../vendor/highlight/core.min.js')
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

function grammar(hljs, language) {
  if (!grammars.has(language)) {
    grammars.set(language, import(`../vendor/highlight/languages/${language}.min.js`)
      .then(m => { hljs.registerLanguage(language, m.default); return true; })
      .catch(() => false));
  }
  return grammars.get(language);
}

/* Colour every code block under `root` that names a language we have.

   Idempotent: a block is marked once it has been through, so re-rendering a
   record does not highlight the same text twice. Blocks are read before the
   first await, so a dialog that closes mid-load simply updates nothing. */
export async function highlightIn(root) {
  const blocks = [...root.querySelectorAll('code[data-lang]:not([data-hl])')];
  if (!blocks.length) return;
  const hljs = await engine();
  if (!hljs) return;
  await Promise.all(blocks.map(async block => {
    const language = canonicalLanguage(block.dataset.lang);
    block.dataset.hl = 'done';
    if (!language || !(await grammar(hljs, language))) return;
    try {
      block.innerHTML = hljs.highlight(block.textContent, { language }).value;
    } catch {
      /* a grammar that throws on this text leaves it as it was */
    }
  }));
}
