/* Per-view teardown: a view registers cleanup for what it puts outside #view (window listeners,
   body-level bars, timers), and the router runs it on the next swap. */

let hooks: Array<() => void> = [];

export const onTeardown = (fn: () => void): void => { hooks.push(fn); };

export function teardownView(): void {
  const run = hooks;
  hooks = [];
  for (const fn of run) {
    /* one broken cleanup must not strand the rest */
    try { fn(); } catch (err) { console.error(err); }
  }
}
