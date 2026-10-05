/* Per-view teardown: a view registers cleanup for what it puts outside #view (window listeners,
   body-level bars, timers), and the router runs it on the next swap. */

let hooks = [];

export const onTeardown = fn => { hooks.push(fn); };

export function teardownView() {
  const run = hooks;
  hooks = [];
  for (const fn of run) {
    /* one broken cleanup must not strand the rest */
    try { fn(); } catch (err) { console.error(err); }
  }
}
