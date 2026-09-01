/* The store switch in the topbar, and the dialog that sends memories to
   another store.

   A store is one SQLite file. The dashboard reads and writes the one the
   home's `active` file names, and so does every MCP server on this machine
   from its next call on. The switch lists the stores, changes the active one
   and offers to create a new one. A switch re-runs the current route: every
   view fetches what it shows, so re-rendering it IS reading the other store.

   It refuses to switch while a dialog is open, for the reason the language
   switch does: the form on screen belongs to the store it was opened on. */

import { $, esc, fmtInt, debounce } from './dom.js';
import { api } from './api.js';
import { toast, failed, modalOpen, openModal, closeModal, promptModal } from './ui.js';
import { pickerFor, pickerValue, setPickerValue, wirePicker, fixedItems } from './pick.js';
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
  title: t('store.count', { name: s.name, n: fmtInt(s.memories) }),
});
const newItem = () => ({ value: NEW, label: t('store.new'), cls: 'pick-new' });
const items = () => [...rows.map(storeItem), newItem()];

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

/* ─── sending memories to another store ──────────────────────────────────
   Offered from the bulk bar (a selection) and from the Domains view (a
   subtree). The dialog asks the server for a dry run as soon as a target is
   named and shows what would move and what the copy cannot carry -- the
   relations, diagram links and jumps, supersedes marks and [[uid]]
   references that cross the edge of the selection -- before the button is
   live. The move is the same request with dry_run off. Resolves to whether
   anything moved. */

export async function moveToStoreModal({ uids = [], domain = '' }) {
  let data;
  try { data = await api('/api/stores'); }
  catch (err) { failed('err.store', err); return false; }
  const others = data.stores.filter(s => s.name !== data.active);
  const targets = [...others.map(storeItem), newItem()];
  const first = others.length ? others[0].name : NEW;
  const scope = domain
    ? t('mv.scope.domain', { domain: esc(domain) })
    : t('mv.scope.uids', { n: fmtInt(uids.length) });

  return new Promise(resolve => {
    let settled = false;
    const done = moved => { if (settled) return; settled = true; closeModal(); resolve(moved); };
    const m = openModal({
      title: t('mv.title'),
      bodyHTML: `
        <p class="hint">${t('mv.body', { from: esc(data.active) })}</p>
        <div class="field"><label>${t('mv.target')}</label>
          <div class="mv-target">
            ${pickerFor({ id: 'mvTarget', value: first, items: targets, ariaLabel: t('mv.target') })}
            <input type="text" data-new placeholder="acme" autocomplete="off" spellcheck="false"
                   aria-label="${t('store.create.label')}"${first === NEW ? '' : ' hidden'}>
          </div></div>
        <div class="mv-plan" data-plan><div>${scope}</div></div>`,
      footHTML: `<button class="btn" data-x>${t('common.cancel')}</button>
                 <button class="btn btn-solid" data-ok disabled>${t('mv.ok')}</button>`,
    });
    const okBtn = m.querySelector('[data-ok]');
    const plan = m.querySelector('[data-plan]');
    const input = m.querySelector('[data-new]');
    const creating = () => pickerValue(m, 'mvTarget') === NEW;
    const target = () => (creating() ? input.value.trim().toLowerCase() : pickerValue(m, 'mvTarget'));
    const body = dry => ({ target: target(), uids, domain, dry_run: dry, create: creating() });

    const preview = async () => {
      const name = target();
      okBtn.disabled = true;
      if (!name) { plan.innerHTML = `<div>${scope}</div><div class="hint">${t('mv.nameIt')}</div>`; return; }
      try {
        const r = await api('/api/stores/move', { body: body(true) });
        if (target() !== name) return;   /* the field moved on while this was in flight */
        plan.innerHTML = planHTML(r, scope);
        okBtn.disabled = r.memories - r.conflicts.length <= 0;
      } catch (err) {
        plan.innerHTML = `<div>${scope}</div><div class="warn-line">${esc(err.message)}</div>`;
      }
    };
    wirePicker(m, { id: 'mvTarget', items: fixedItems(targets), onPick: value => {
      input.hidden = value !== NEW;
      if (value === NEW) input.focus();
      preview();
    } });
    input.addEventListener('input', debounce(preview, 300));
    m.querySelector('[data-x]').onclick = () => done(false);
    okBtn.onclick = async () => {
      okBtn.disabled = true;
      try {
        const r = await api('/api/stores/move', { body: body(false) });
        toast(t('mv.done', { n: fmtInt(r.moved), target: esc(r.target) }), 'ok',
              r.backup ? { detail: t('mv.backup', { name: r.backup.split(/[\\/]/).pop() }) } : {});
        /* the caller repaints its view; the rail's counts are this store's
           and just changed too */
        if (r.moved > 0) refreshRail();
        done(r.moved > 0);
      } catch (err) { okBtn.disabled = false; failed('err.move', err); }
    };
    preview();
  });
}

/* The dry run as lines: what moves, then everything the copy leaves behind. */
function planHTML(r, scope) {
  const lines = [scope, t('mv.plan', {
    m: fmtInt(r.memories), d: fmtInt(r.diagrams), r: fmtInt(r.relations), e: fmtInt(r.edits),
  })];
  if (r.creates) lines.push(t('mv.creates', { target: esc(r.target) }));
  const warn = text => lines.push(`<span class="warn-line">${text}</span>`);
  if (r.conflicts.length) warn(t('mv.conflicts', { n: fmtInt(r.conflicts.length) }));
  if (r.unknown.length) warn(t('mv.unknown', { n: fmtInt(r.unknown.length) }));
  const o = r.outside;
  const crossings = [
    ['relations', o.relations.count],
    ['diagrams', o.diagram_links.count + o.diagram_jumps.count],
    ['superseded', o.superseded_by.count],
    ['body', o.body_links.count],
  ];
  for (const [key, n] of crossings) if (n) warn(t(`mv.outside.${key}`, { n: fmtInt(n) }));
  return lines.map(l => `<div>${l}</div>`).join('');
}
