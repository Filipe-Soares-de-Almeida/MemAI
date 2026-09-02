/* The relations graph arranges itself in here, off the main thread.

   The arithmetic is all in graph-layout.js -- this file is the driver: it
   slices the settle so a message can still be delivered between passes, and
   posts positions on a cadence so the engine can draw the graph
   condensing instead of waiting on a blank frame.

   Protocol, both ways:

     in   { t: 'start', count, degree, ea, eb }   buffers are transferred in
          { t: 'stop' }                           the view is being left

     out  { t: 'pos', pos, progress, done }       a copy, transferred out
          { t: 'fail', msg }                      the layout threw

   `pos` is three floats per node in node order, and the node order is
   whatever order the caller sent -- nothing in here knows a uid. */

import { Layout } from './graph-layout.js';

/* How long one slice may hold the worker before it yields. Nothing is
   waiting on those milliseconds, but a `stop` message has to be able to
   arrive while a large store is still settling. */
const SLICE_MS = 14;
/* how often positions go out while it settles */
const POST_MS = 90;
/* The wall-clock ceiling on one settle. A store big enough to reach this has
   an arrangement good enough to read long before it is finished polishing
   it, and a worker spinning for a minute behind a bar at 60% is worse than a
   graph that stops moving. */
const DEADLINE_MS = 20000;

let layout = null;
let stopped = false;

const send = done => {
  postMessage({
    t: 'pos',
    pos: layout.pos.slice(),
    progress: done ? 1 : layout.progress,
    done,
  });
};

function run(startedAt, postedAt) {
  if (stopped) return;
  const slice = performance.now();
  let live = true;
  while (live && performance.now() - slice < SLICE_MS) live = layout.pass();

  const now = performance.now();
  const done = !live || now - startedAt > DEADLINE_MS;
  if (done || now - postedAt >= POST_MS) {
    send(done);
    postedAt = now;
  }
  if (!done) setTimeout(() => run(startedAt, postedAt), 0);
}

onmessage = e => {
  const msg = e.data;
  if (msg.t === 'stop') { stopped = true; layout = null; return; }
  if (msg.t !== 'start') return;
  if (!msg.count) {
    /* an empty scope has an arrangement already, and the grid pass would
       take the bounds of nothing */
    postMessage({ t: 'pos', pos: new Float32Array(0), progress: 1, done: true });
    return;
  }
  try {
    stopped = false;
    layout = new Layout(msg.count, msg.degree, msg.ea, msg.eb);
    layout.seed();
    const at = performance.now();
    send(false);
    run(at, at);
  } catch (err) {
    postMessage({ t: 'fail', msg: String(err && err.message || err) });
  }
};
