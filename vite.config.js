import { readdir, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import { packageVersion, sourceHash } from './tools/build-stamp.mjs';

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

/* Writes the licence of every bundled runtime dependency into the build, read from node_modules
   so it matches the bundled version; devDependencies never reach the browser. */
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

/* Writes dist/build.json, the stamp memai.webui_build compares to decide
   whether the build still matches its sources. */
function buildStamp() {
  return {
    name: 'memai:build-stamp',
    apply: 'build',
    async generateBundle() {
      const root = fileURLToPath(new URL('./', import.meta.url));
      const stamp = { version: await packageVersion(root), sources: await sourceHash(root) };
      this.emitFile({ type: 'asset', fileName: 'build.json', source: `${JSON.stringify(stamp, null, 2)}
` });
    },
  };
}

export default defineConfig({
  plugins: [thirdPartyNotices(), buildStamp()],
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
    /* the highlight.js chunk is ~1 MB and is read from loopback on the first
       code block, so the default 500 kB warning says nothing useful here */
    chunkSizeWarningLimit: 1200,
  },
  server: {
    /* Proxy the API and /fonts.css to memai.admin so writes stay same-origin (SameOriginMiddleware). */
    proxy: {
      '/api': admin,
      '/fonts.css': admin,
    },
  },
});
