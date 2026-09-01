/* The store switch in the topbar.

   A store is one SQLite file. The dashboard reads and writes the one the
   home's `active` file names, and so does every MCP server on this machine
   from its next call on. This control lists the stores, switches the active
   one and offers to create a new one. A switch re-runs the current route:
   every view fetches what it shows, so re-rendering it IS reading the other
   store.

   It refuses to switch while a dialog is open, for the reason the language
   switch does: the form on screen belongs to the store it was opened on. */

import { $ } from './dom.js';
import { api } from './api.js';
import { toast, failed, modalOpen, promptModal } from './ui.js';
import { pickerFor, setPickerValue, wirePicker, fixedItems } from './pick.js';
import { route, parseHash } from './router.js';
import { refreshRail } from './shared.js';
import { t } from '../i18n.js';

/* The row that is an action rather than a store. A store name is lowercase
   letters, digits, '.', '_' and '-', so no store can be called this. */
const NEW = '+new';

let active = '';
let rows = [];

const storeItem = s => ({
  value: s.name, label: s.name,
  title: t('store.count', { name: s.name, n: s.memories }),
});
const items = () => [...rows.map(storeItem), { value: NEW, label: t('store.new'), cls: 'pick-new' }];

export async function mountStorePicker() {
  if (!$('#storeHost')) return;
  try { paint(await api('/api/stores')); }
  catch (err) { failed('err.store', err); }
}

function paint(data) {
  active = data.active;
  rows = data.stores;
  const list = items();
  $('#storeHost').innerHTML = pickerFor({
    id: 'storeSel', value: active, items: list,
    ariaLabel: t('store.title'), cls: 'store-sel',
  });
  wirePicker(document, { id: 'storeSel', items: fixedItems(list), onPick: pick });
}

/* The button has already repainted itself to the row that was clicked, so a
   refusal puts the active store's face back before saying why. */
const revert = () => setPickerValue($('#storeSel'), items().find(it => it.value === active));

async function pick(value) {
  if (value === active) return;
  if (modalOpen()) { revert(); toast(t('store.busy'), 'bad'); return; }
  if (value === NEW) { revert(); await create(); return; }
  try {
    await api('/api/stores/active', { body: { name: value } });
    active = value;
    toast(t('store.switched', { name: value }), 'ok');
    reread();
  } catch (err) { revert(); failed('err.store', err); }
}

async function create() {
  const name = await promptModal({
    title: t('store.create.title'), body: t('store.create.body'),
    label: t('store.create.label'), placeholder: 'acme', okLabel: t('store.create.ok'),
  });
  if (name === null) return;
  try {
    const data = await api('/api/stores', { body: { name: name.trim(), activate: true } });
    paint(data);
    toast(t('store.created', { name: data.active }), 'ok');
    reread();
  } catch (err) { failed('err.store', err); }
}

/* The view repaints against the store that is active now. Overview hands its
   own payload to the rail; every other view needs the rail fetched apart. */
function reread() {
  route();
  if (parseHash().name !== 'overview') refreshRail();
}
