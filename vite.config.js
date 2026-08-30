import { readdir, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

/* The dashboard's sources sit inside the Python package. memai.admin serves
   the build output, webui/dist, under /static -- hence root and base. */
const webui = fileURLToPath(new URL('src/memai/webui/', import.meta.url));

const admin = `http://127.0.0.1:${process.env.MEMAI_ADMIN_PORT || '8888'}`;

const NOTICES = 'THIRD-PARTY-NOTICES.txt';
const RULE = '='.repeat(70);

/* The licence text a package ships, by whichever of the usual names it uses. */
async function licenceOf(dir) {
  const found = (await readdir(dir)).find(name => /^licen[cs]e/i.test(name));
  if (!found) throw new Error(`no licence file in ${dir}`);
  return (await readFile(new URL(found, dir), 'utf8')).trim();
}

/* Writes the licence of every bundled dependency into the build.

   The wheel ships dist/ and the bundle carries those packages' code, so their
   notices travel with it. The text is read out of node_modules at build time,
   so it belongs to the version actually bundled. devDependencies are left
   out: none of them reaches the browser. */
function thirdPartyNotices() {
  return {
    name: 'memai:third-party-notices',
    apply: 'build',
    async generateBundle() {
      const root = new URL('./', import.meta.url);
      const { dependencies = {} } = JSON.parse(
        await readFile(new URL('package.json', root), 'utf8'));

      const sections = [];
      for (const name of Object.keys(dependencies).sort()) {
        const dir = new URL(`node_modules/${name}/`, root);
        const meta = JSON.parse(await readFile(new URL('package.json', dir), 'utf8'));
        const head = `${name} ${meta.version} -- ${meta.license}`;
        sections.push([head, '', await licenceOf(dir)].join('\n'));
      }

      const lines = [
        'The MemAI dashboard bundles the packages below.',
        'Each is covered by its own licence, reproduced in full.',
        '',
        sections.join(['', '', RULE, '', ''].join('\n')),
        '',
      ];
      this.emitFile({ type: 'asset', fileName: NOTICES, source: lines.join('\n') });
    },
  };
}

export default defineConfig({
  plugins: [thirdPartyNotices()],
  root: webui,
  base: '/static/',
  /* Copied verbatim, never hashed: the fonts and the locale catalogs are both
     fetched by name at runtime. */
  publicDir: 'public',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    /* i18n.js awaits its catalog at module scope */
    target: 'es2022',
  },
  server: {
    /* The API and the generated /fonts.css come from memai.admin. Proxying
       them keeps every request same-origin, which its SameOriginMiddleware
       needs to let a write through. */
    proxy: {
      '/api': admin,
      '/fonts.css': admin,
    },
  },
});
