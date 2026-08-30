/* Syntax highlighting for the fenced code blocks a memory's body carries.

   The engine is highlight.js, an npm dependency bundled into the build.
   Nothing is loaded until a block asks for it: the core comes in on the
   first highlighted block, each grammar on the first block written in that
   language, and a page with no code block loads neither. Every entry in
   GRAMMARS is its own lazy chunk.

   A language with no entry leaves the block plain monospace, which is also
   what happens when a load fails. The text is already on screen by then,
   escaped, so the worst case is colourless -- never empty, never mangled.

   Adding a language is one line in GRAMMARS and, if it goes by more than one
   name, one in ALIASES. The specifier has to be written out: a bundler
   cannot follow an import path assembled at runtime. */

export const GRAMMARS = {
  bash: () => import('highlight.js/lib/languages/bash'),
  delphi: () => import('highlight.js/lib/languages/delphi'),
  diff: () => import('highlight.js/lib/languages/diff'),
  ini: () => import('highlight.js/lib/languages/ini'),
  javascript: () => import('highlight.js/lib/languages/javascript'),
  json: () => import('highlight.js/lib/languages/json'),
  powershell: () => import('highlight.js/lib/languages/powershell'),
  python: () => import('highlight.js/lib/languages/python'),
  sql: () => import('highlight.js/lib/languages/sql'),
  xml: () => import('highlight.js/lib/languages/xml'),
  yaml: () => import('highlight.js/lib/languages/yaml'),
};

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

function grammar(hljs, language) {
  if (!grammars.has(language)) {
    const load = GRAMMARS[language];
    grammars.set(language, !load ? Promise.resolve(false)
      : load()
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
