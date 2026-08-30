/* The set of languages highlight.js ships, read from the installed package.

   Two consumers share it so neither carries a list of its own: the Vite
   plugin that generates the dashboard's grammar map, and
   tools/highlight-cases.mjs, which colours a snippet in each language it has
   one for.

   Usage: import { languages, aliases } from './hljs-catalog.mjs' */

import { readdir } from 'node:fs/promises';

const DIR = new URL('../node_modules/highlight.js/es/languages/', import.meta.url);

/* Every grammar name, which is also the name highlight.js registers it under
   and the subpath that imports it.

   The package ships a `<name>.js.js` beside each grammar: a shim that logs a
   deprecation notice and re-exports. Those are skipped. */
export async function languages() {
  return (await readdir(DIR))
    .filter(name => name.endsWith('.js') && !name.endsWith('.js.js'))
    .map(name => name.slice(0, -3))
    .sort();
}

/* alias -> language, from what each grammar declares about itself.

   A definition is a function of an hljs instance, so reading its aliases
   means calling it. One that throws contributes no alias and stays
   reachable under its own name. Loaded by file URL rather than by the
   package's subpath export, which node warns about once per import. */
export async function aliases(names) {
  const hljs = (await import('highlight.js/lib/core')).default;
  const table = {};
  for (const language of names) {
    try {
      const define = (await import(new URL(`${language}.js`, DIR))).default;
      for (const alias of define(hljs).aliases || []) {
        const key = String(alias).toLowerCase();
        if (!names.includes(key)) table[key] = language;
      }
    } catch { /* unreadable definition, reachable by name */ }
  }
  return Object.fromEntries(Object.entries(table).sort());
}
