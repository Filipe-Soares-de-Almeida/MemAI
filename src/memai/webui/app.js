/* Dashboard boot: the view table, registered with the router here so no view imports the
   router's importer, and the global keyboard shortcuts. */

/* first, so the root carries data-motion before anything is drawn */
import './core/motion.ts';
import { $ } from './core/dom.ts';
import { paintIcons } from './core/icons.js';
import { modalOpen, closeModal, toast } from './core/ui.js';
import { pickerFor, setPickerValue, wirePicker, fixedItems } from './core/pick.js';
import { mountProjectPicker } from './core/projects.js';
import { mountVersionChip } from './core/version.ts';
import { registerViews, route } from './core/router.ts';
import { mountChrome } from './core/chrome.ts';
import { I18N, t } from './i18n.ts';

import OverviewView from './views/overview/OverviewView.vue';
import MemoriesView from './views/memories/MemoriesView.vue';
import GraphView from './views/graph/GraphView.vue';
import DiagramsView from './views/diagrams/DiagramsView.vue';
import DiagramView from './views/diagram/DiagramView.vue';
import DomainsView from './views/domains/DomainsView.vue';
import MaintenanceView from './views/maintenance/MaintenanceView.vue';
import OptimizationView from './views/optimization/OptimizationView.vue';
import ChangelogView from './views/changelog/ChangelogView.vue';
import RecordView from './views/record/RecordView.vue';
import { openRecord } from './core/nav.ts';
import { openNewMemory } from './views/new-memory/index.ts';

registerViews({
  overview: OverviewView,
  memories: MemoriesView,
  graph: GraphView,
  diagrams: DiagramsView,
  diagram: DiagramView,
  domains: DomainsView,
  maintenance: MaintenanceView,
  optimization: OptimizationView,
  changelog: ChangelogView,
  memory: RecordView,
}, { onRecord: openRecord });

mountChrome();

/* draw the shell's icons before the first route, so the app bar is never
   shown mid-assembly (i18n does the same for its text, at import time) */
paintIcons();
/* the project switch in the bar needs a fetch of its own, so it fills in
   when that lands */
mountProjectPicker();
/* the version mark does the same: one read of the release check's cache,
   painted into the bar when it lands */
mountVersionChip();

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
