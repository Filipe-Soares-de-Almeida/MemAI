/* Per-view teardown.

   A view swap is `#view.innerHTML = ...`, which drops that subtree and
   every listener on it -- but not the things a view puts OUTSIDE it: a
   canvas engine listening on window, a floating selection bar parented
   to document.body, a timer. Those used to be cleaned up by name in the
   router, which meant the router had to know about every view, and the
   one it did not know about (the bulk bar) leaked: selecting rows and
   then navigating away left the bar on screen, still acting on a
   selection whose list was gone.

   So a view registers its own cleanup, and the router just runs them. */

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
