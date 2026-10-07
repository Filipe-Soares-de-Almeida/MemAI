import { defineConfig } from 'vitest/config';
import vue from '@vitejs/plugin-vue';

/* The dashboard's behaviour tests: browser modules run in happy-dom, with the
   API and the locale catalogs answered by tests/webui/support.js. */
export default defineConfig({
  plugins: [vue()],
  test: {
    include: ['tests/webui/**/*.test.{js,ts}'],
    environment: 'happy-dom',
    setupFiles: ['tests/webui/setup.js'],
  },
});
