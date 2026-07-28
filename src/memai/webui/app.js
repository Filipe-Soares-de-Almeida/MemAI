/* MemAI admin SPA — boot.

   This file owns the view table, the global keyboard shortcuts and
   nothing else. Everything it wires together lives in:

     core/dom.js        formatting and DOM helpers
     core/api.js        the one door to the JSON API in memai/admin.py
     core/ui.js         toasts, hover tip, modals, context menu
     core/shared.js     types, confidence scale, shared fragments, domains
     core/lifecycle.js  per-view teardown
     core/router.js     hash routing, generation-counted renders
     views/*.js         one module per section
     diagram-engine.js  the diagram canvas (no DOM outside its canvas)

   The view table is registered from HERE rather than declared in the
   router so that no view has to import the router's importer: views
   import { go, refreshBehind } and the cycle stays broken. */

import { $ } from './core/dom.js';
import { api } from './core/api.js';
import { modalOpen, closeModal } from './core/ui.js';
import { updateRail } from './core/shared.js';
import { registerViews, route, go } from './core/router.js';

import { renderOverview } from './views/overview.js';
import { renderMemories } from './views/memories.js';
import { renderGraph } from './views/graph.js';
import { renderDiagrams } from './views/diagrams.js';
import { renderDiagram } from './views/diagram.js';
import { renderDomains } from './views/domains.js';
import { renderMaintenance } from './views/maintenance.js';
import { renderOptimization } from './views/optimization.js';
import { openRecord, closeDrawer, drawerOpen } from './views/record.js';
import { openNewMemory } from './views/new-memory.js';

registerViews({
  overview: renderOverview,
  memories: renderMemories,
  graph: renderGraph,
  diagrams: renderDiagrams,
  diagram: renderDiagram,
  domains: renderDomains,
  maintenance: renderMaintenance,
  optimization: renderOptimization,
}, { onRecord: openRecord });

$('#btnNew').addEventListener('click', openNewMemory);

$('#globalSearch').addEventListener('keydown', e => {
  if (e.key === 'Enter' && e.target.value.trim()) {
    go('memories', { q: e.target.value.trim(), status: '' });
    e.target.value = '';
    e.target.blur();
  }
});

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    /* innermost first: a modal opened from the drawer closes back to it */
    if (modalOpen()) { closeModal(); return; }
    if (drawerOpen()) closeDrawer();
    return;
  }
  if (e.key === '/' && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {
    e.preventDefault();
    $('#globalSearch').focus();
  }
});

addEventListener('hashchange', route);
route();
/* rail health on first paint, independent of the landing view */
api('/api/overview').then(updateRail).catch(() => {});
