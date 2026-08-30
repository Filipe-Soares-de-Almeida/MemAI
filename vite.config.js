import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

/* The dashboard's sources sit inside the Python package. memai.admin serves
   the build output, webui/dist, under /static -- hence root and base. */
const webui = fileURLToPath(new URL('src/memai/webui/', import.meta.url));

const admin = `http://127.0.0.1:${process.env.MEMAI_ADMIN_PORT || '8888'}`;

export default defineConfig({
  root: webui,
  base: '/static/',
  /* Copied verbatim, never hashed: the fonts, the locale catalogs and the
     vendored highlight.js grammars are all fetched by name at runtime. */
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
