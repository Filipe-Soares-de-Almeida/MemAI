import { defineConfig } from 'vitest/config';

/* The dashboard's behaviour tests: browser modules run in happy-dom, with the
   API and the locale catalogs answered by tests/webui/support.js. */
export default defineConfig({
  test: {
    include: ['tests/webui/**/*.test.js'],
    environment: 'happy-dom',
    setupFiles: ['tests/webui/setup.js'],
  },
});
