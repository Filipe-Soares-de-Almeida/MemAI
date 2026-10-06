/* A Vue component as a routed view: one app per view, resolved once its async setup has loaded,
   and unmounted on the next view swap. */

import { createApp, defineComponent, h, Suspense } from 'vue';
import type { Component } from 'vue';
import { onTeardown } from './lifecycle.ts';
import { failed } from './toasts.ts';
import type { ViewContext } from './router.ts';

/* The props every routed component is mounted with. */
export interface ViewProps {
  params: URLSearchParams;
  ctx: ViewContext;
}

/* Resolves when the view has loaded; rejects with a load error, which the router draws as its
   failure panel. */
export function mountView(view: Component, host: HTMLElement, params: URLSearchParams,
                          ctx: ViewContext): Promise<void> {
  return new Promise((resolve, reject) => {
    let loaded = false;
    const app = createApp(defineComponent({
      setup: () => () => h(Suspense, { onResolve: () => { loaded = true; resolve(); } }, {
        default: () => h(view, { params, ctx }),
        fallback: () => h('div', { class: 'loading' }, h('span', { class: 'spin' })),
      }),
    }));
    app.config.errorHandler = err => {
      if (!loaded) {
        loaded = true;
        app.unmount();
        reject(err);
        return;
      }
      console.error(err);
      failed('err.unexpected', err);
    };
    onTeardown(() => {
      app.unmount();
      if (!loaded) { loaded = true; resolve(); }
    });
    app.mount(host);
  });
}
