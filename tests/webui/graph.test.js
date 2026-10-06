import { describe, expect, it } from 'vitest';
import { GraphCanvas } from '../../src/memai/webui/engines/graph-2d.ts';
import { renderGraph } from '../../src/memai/webui/views/graph.js';
import { catalog, serveApi } from './support.js';

const en = catalog('en');
const ctx = { stale: () => false };

describe('the graph canvas', () => {
  it('animates by the root data-motion, whatever the system preference says', () => {
    const motion = Object.getOwnPropertyDescriptor(GraphCanvas.prototype, 'motion').get;
    const root = document.documentElement;
    const was = root.dataset.motion;
    window.matchMedia = () => ({ matches: true, addEventListener() {} });
    root.dataset.motion = 'full';
    expect(motion.call({})).toBe(true);
    root.dataset.motion = 'reduce';
    expect(motion.call({})).toBe(false);
    root.dataset.motion = was;
  });
});

describe('the graph view', () => {
  it('labels its filters like the memory list, above a canvas that sits in the view wrapper', async () => {
    serveApi(path => {
      if (path === '/api/domains') return { domains: [] };
      if (path.startsWith('/api/graph?')) return { nodes: [], edges: [], total: 0, truncated: false };
      return {};
    });
    const view = document.getElementById('view');
    await renderGraph(view, new URLSearchParams(), ctx);
    const labels = [...view.querySelectorAll('.graph-bar .tb-field .mg-label')].map(l => l.textContent);
    expect(labels).toEqual(['g.f.find', 'mem.f.domain', 'mem.f.type', 'mem.f.status', 'g.f.show']
      .map(k => en[k]));
    expect(view.querySelector(':scope > .anim > .graph-wrap#gWrap')).not.toBeNull();
  });
});
