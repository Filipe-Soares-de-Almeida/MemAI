/* MemAI admin SPA — boot.

   This file owns the view table, the global keyboard shortcuts and
   nothing else. Everything it wires together lives in:

     core/dom.js        formatting and DOM helpers
     core/api.js        the one door to the JSON API in memai/admin.py
     core/ui.js         toasts, hover tip, modals, context menu
     core/icons.js      every icon in the UI
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
import { paintIcons } from './core/icons.js';
import { modalOpen, closeModal, confirmModal, toast } from './core/ui.js';
import { updateRail } from './core/shared.js';
import { registerViews, route, go, parseHash } from './core/router.js';
import { I18N, t } from './i18n.js';

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

/* draw the shell's icons before the first route, so the rail is never
   shown mid-assembly (i18n does the same for its text, at import time) */
paintIcons();

$('#btnNew').addEventListener('click', openNewMemory);

/* Language. The reload that applies it discards whatever is on screen and
   unsaved, so it asks -- except when a modal is holding the form, because
   opening a confirmation would itself close that modal. Then it waits. */
$('#langSel').addEventListener('change', async e => {
  const code = e.target.value;
  if (modalOpen()) {
    e.target.value = I18N.locale;
    toast(t('lang.busy'), 'bad');
    return;
  }
  if (drawerOpen() && !(await confirmModal({
    title: t('lang.switch.title'),
    body: t('lang.switch.body'),
    okLabel: t('lang.switch.ok'),
  }))) {
    e.target.value = I18N.locale;
    return;
  }
  I18N.set(code);
});

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

/* wrapped, so the hashchange Event is not read as route's options */
addEventListener('hashchange', () => route());
route();
/* Rail health on first paint. Overview renders from /api/overview and hands
   the same payload to updateRail itself, so asking for it here as well put
   two copies of one request in flight on the landing view. */
if (parseHash().name !== 'overview') api('/api/overview').then(updateRail).catch(() => {});
