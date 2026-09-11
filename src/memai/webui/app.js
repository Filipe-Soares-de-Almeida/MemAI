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
import { paintIcons } from './core/icons.js';
import { modalOpen, closeModal, toast } from './core/ui.js';
import { pickerFor, setPickerValue, wirePicker, fixedItems } from './core/pick.js';
import { mountProjectPicker } from './core/projects.js';
import { registerViews, route } from './core/router.js';
import { I18N, t } from './i18n.js';

import { renderOverview } from './views/overview.js';
import { renderMemories } from './views/memories.js';
import { renderGraph } from './views/graph.js';
import { renderDiagrams } from './views/diagrams.js';
import { renderDiagram } from './views/diagram.js';
import { renderDomains } from './views/domains.js';
import { renderMaintenance } from './views/maintenance.js';
import { renderOptimization } from './views/optimization.js';
import { renderRecord, openRecord } from './views/record.js';
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
  memory: renderRecord,
}, { onRecord: openRecord });

/* draw the shell's icons before the first route, so the app bar is never
   shown mid-assembly (i18n does the same for its text, at import time) */
paintIcons();
/* the project switch in the bar needs a fetch of its own, so it fills in
   when that lands */
mountProjectPicker();

$('#btnNew').addEventListener('click', openNewMemory);

/* The language switch. Applying a language reloads the page, which discards
   whatever a form is holding, so it refuses while a dialog is open. */
const langItems = Object.entries(I18N.locales).map(([value, label]) => ({ value, label }));
$('#langHost').innerHTML = pickerFor({
  id: 'langSel', value: I18N.locale, items: langItems,
  ariaLabel: t('lang.title'), cls: 'lang-sel',
});
wirePicker(document, { id: 'langSel', items: fixedItems(langItems), onPick: code => {
  if (code === I18N.locale) return;
  if (modalOpen()) {
    /* the picker has already repainted itself to the language that is not
       going to be loaded, so put it back before saying why */
    setPickerValue($('#langSel'), langItems.find(it => it.value === I18N.locale));
    toast(t('lang.busy'), 'bad');
    return;
  }
  I18N.set(code);
}, align: 'right' });

document.addEventListener('keydown', e => {
  /* One level, innermost first: a sub-form opened from the record closes
     back to the record rather than dismissing both. */
  if (e.key === 'Escape' && modalOpen()) closeModal();
});

/* wrapped, so the hashchange Event is not read as route's options */
addEventListener('hashchange', () => route());
route();
