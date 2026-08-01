/* Keep the two edge-routing implementations honest about each other.
 *
 *     node tools/route-parity.mjs            check
 *     node tools/route-parity.mjs --write    re-record the golden
 *
 * webui/diagram-engine.js routes edges for the canvas; memai/diagram_svg.py
 * routes them for the SVG export. Neither derives from the other (see the
 * header of the Python module for why), so this script runs a shared
 * fixture through the JAVASCRIPT one and records the answer. The Python
 * test suite reads the same recording. Whichever side is edited alone, one
 * of the two goes red.
 *
 * The canvas is the reference, not the arbiter of taste: it is what a user
 * arranges a diagram against, so it is what the export has to match.
 *
 * The editor is constructed for real -- with a stub DOM -- rather than
 * having setData's work replicated here. Replicating it is how a parity
 * harness ends up agreeing with itself about something the browser does
 * differently.
 */

import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const FIXTURE = join(ROOT, 'tests', 'fixtures', 'route-fixture.json');
const GOLDEN = join(ROOT, 'tests', 'fixtures', 'route-golden.json');
const ENGINE = join(ROOT, 'src', 'memai', 'webui', 'diagram-engine.js');

/* ── the smallest DOM the editor will start against ──────────────────── */

/* Nothing here is asked to behave like a browser: the routing code never
 * touches the canvas, and the drawing code never runs because the stubbed
 * requestAnimationFrame does not call back. measureText is present only
 * because wrap() would reach for it -- text metrics are checked on the
 * Python side against real browser numbers, not here. */
const stubCanvas = () => {
  const ctx = {
    measureText: t => ({ width: String(t).length * 6 }),
    setTransform() {}, save() {}, restore() {}, beginPath() {}, closePath() {},
    moveTo() {}, lineTo() {}, arcTo() {}, arc() {}, rect() {}, roundRect() {},
    quadraticCurveTo() {}, fill() {}, stroke() {}, fillText() {},
    fillRect() {}, strokeRect() {}, clearRect() {}, setLineDash() {},
  };
  return {
    width: 0, height: 0, style: {},
    getContext: () => ctx,
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 1200, height: 800 }),
    addEventListener() {}, removeEventListener() {},
    setPointerCapture() {}, releasePointerCapture() {},
    classList: { add() {}, remove() {}, toggle() {} },
    parentElement: { clientWidth: 1200, clientHeight: 800 },
  };
};

globalThis.devicePixelRatio = 1;
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};
globalThis.document = { documentElement: {} };
globalThis.getComputedStyle = () => ({ getPropertyValue: () => '' });

/* ── record ──────────────────────────────────────────────────────────── */

const round = n => Math.round(n * 1e6) / 1e6;   /* kill float noise, keep sub-pixel */
const pt = p => ({ x: round(p.x), y: round(p.y) });

const record = async data => {
  const { DiagramEditor, BADGE_PX } = await import(`file://${ENGINE}`);
  const ed = new DiagramEditor(stubCanvas(), data, { readOnly: true });
  return {
    font_scale: round(ed.fontScale),
    lane_span: ed.laneSpan
      ? { left: round(ed.laneSpan.left), right: round(ed.laneSpan.right) }
      : null,
    orphans: [...ed.orphans].sort(),
    nodes: ed.nodes.map(n => ({
      key: n.key, shape: n.shape, sized: n.sized,
      x: round(n.x), y: round(n.y), w: round(n.w), h: round(n.h),
      /* both rows, because halfWidthAt is NOT symmetric in dy on a
         parallelogram -- recording one would leave the other unpinned */
      badge: [-1, 1].map(row => {
        const at = DiagramEditor.badgeAnchor(n, row, BADGE_PX * ed.fontScale);
        return { left: round(at.left), mid_y: round(at.midY) };
      }),
    })),
    edges: ed.edges.map(e => {
      const pts = ed.route(e);
      return {
        from: e.from, to: e.to,
        back: !!e.back, loops: !!e.loops,
        lane: e.lane || 0, bow: round(e.bow || 0), flank: !!e.flank,
        via: e.via
          ? (e.via.corridor !== undefined
              ? { corridor: round(e.via.corridor) }
              : e.via.crossY !== undefined
                ? { crossY: round(e.via.crossY) }
                : { crossX: round(e.via.crossX) })
          : null,
        fan_from: round(e.fanFrom || 0), fan_to: round(e.fanTo || 0),
        stub_from: round(e.stubFrom || 0), stub_to: round(e.stubTo || 0),
        corridor: e.lane || e.via?.corridor !== undefined
          ? round(ed.corridorFor(e)) : null,
        /* the whole polyline: the one output that proves the rest agreed */
        pts: pts.map(pt),
        label_at: pts.length ? pt(DiagramEditor.midpoint(pts)) : null,
        short_label: DiagramEditor.shortLabel(e.label),
      };
    }),
  };
};

const main = async () => {
  const data = JSON.parse(readFileSync(FIXTURE, 'utf8'));
  const fresh = {
    _: 'Recorded from webui/diagram-engine.js by tools/route-parity.mjs. Do not hand-edit.',
    ...await record(data),
  };

  if (process.argv.includes('--write')) {
    writeFileSync(GOLDEN, `${JSON.stringify(fresh, null, 1)}\n`);
    console.log(`wrote ${GOLDEN}\n  ${fresh.edges.length} edges`);
    return 0;
  }

  let golden;
  try {
    golden = JSON.parse(readFileSync(GOLDEN, 'utf8'));
  } catch {
    console.error(`no golden yet -- run: node tools/route-parity.mjs --write`);
    return 1;
  }
  /* Compared as canonical JSON rather than field by field: a new field on
     one side is a divergence too, and this way it cannot be forgotten. */
  const a = JSON.stringify(golden), b = JSON.stringify(fresh);
  if (a === b) {
    console.log(`parity ok -- ${fresh.edges.length} edges`);
    return 0;
  }
  console.error('the canvas no longer routes the fixture the way the golden says.');
  console.error('  if the change was intended, re-record with --write and run pytest;');
  console.error('  the Python side has to be updated to match.');
  for (const [i, edge] of fresh.edges.entries()) {
    const was = golden.edges?.[i];
    if (JSON.stringify(was) !== JSON.stringify(edge)) {
      console.error(`  ${edge.from}->${edge.to}`);
      console.error(`    golden ${JSON.stringify(was?.pts)}`);
      console.error(`    now    ${JSON.stringify(edge.pts)}`);
    }
  }
  return 1;
};

process.exit(await main());
