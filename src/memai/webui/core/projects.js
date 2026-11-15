/* The project switch in the app bar, the rename/delete dialog and the move-to-project dialog. A
   switch re-runs the route, which re-reads the new project; it is refused while a dialog is open. */

import { $, esc, fmtInt, debounce } from './dom.ts';
import {
  toast, failed, modalOpen, openModal, closeModal, promptModal, confirmModal, typedConfirmModal,
} from './ui.js';
import { pickerFor, pickerValue, setPickerValue, wirePicker, fixedItems } from './pick.js';
import { route } from './router.ts';
import { t } from '../i18n.ts';
import * as client from '../api/client.ts';

/* The row that is an action rather than a project. A project name is a file
   name, and no file name may carry this character. */
const NEW = '*new';
const MANAGE = '*manage';

let active = '';
let rows = [];

/* The default project wears its role in the list. Its name stays what the
   server calls it, which is what every other surface prints. */
const labelOf = p => (p.general ? t('project.default', { name: esc(p.name) }) : esc(p.name));
const projectItem = p => ({
  value: p.name, html: `<span class="pick-label">${labelOf(p)}</span>`, label: p.name,
  title: t('project.count', { name: p.name, n: fmtInt(p.memories) }),
});
const newItem = () => ({ value: NEW, label: t('project.new'), cls: 'pick-new' });
const manageItem = () => ({ value: MANAGE, label: t('project.manage'), cls: 'pick-new pick-manage' });
const items = () => [...rows.map(projectItem), newItem(), manageItem()];

export async function mountProjectPicker() {
  if (!$('#projectHost')) return;
  try { paint(await client.projects.list()); }
  catch (err) { failed('err.project', err); }
}

function paint(data) {
  active = data.active;
  rows = data.projects;
  const list = items();
  /* the name IS the control -- there is no room in the bar for a label
     beside it, and the caret says the same thing the word would */
  $('#projectHost').innerHTML = pickerFor({
    id: 'projectSel', value: active, items: list,
    ariaLabel: t('project.title'), cls: 'project-sel',
  });
  wirePicker(document, { id: 'projectSel', items: fixedItems(list), onPick: pick,
                         minWidth: 200, panelCls: 'pick-quiet', align: 'right' });
}

/* The button has already repainted itself to the row that was clicked, so a
   refusal puts the active project's face back before saying why. */
const revert = () => setPickerValue($('#projectSel'), items().find(it => it.value === active));

async function pick(value) {
  if (value === active) return;
  if (modalOpen()) { revert(); toast(t('project.busy'), 'bad'); return; }
  if (value === NEW) { revert(); await create(); return; }
  if (value === MANAGE) { revert(); await manage(); return; }
  try {
    const r = await client.projects.activate({ name: value });
    active = r.active;
    toast(t('project.switched', { name: esc(r.active) }), 'ok');
    reread();
  } catch (err) { revert(); failed('err.project', err); }
}

async function create() {
  const name = await promptModal({
    title: t('project.create.title'), body: t('project.create.body'),
    label: t('project.create.label'), placeholder: 'Acme', okLabel: t('project.create.ok'),
  });
  if (name === null) return;
  try {
    const data = await client.projects.create({ name: name.trim(), activate: true });
    paint(data);
    toast(t('project.created', { name: esc(data.active) }), 'ok');
    reread();
  } catch (err) { failed('err.project', err); }
}

/* Every view fetches what it shows, so re-rendering reads the newly active project. */
function reread() {
  route();
}

/* ─── renaming and deleting: the list closes while an action runs, so no two scrims stack, and
   comes back after it. */

async function manage() {
  let data;
  try { data = await client.projects.list(); }
  catch (err) { failed('err.project', err); return; }
  const row = p => {
    const locked = p.general;
    const delTitle = p.active ? t('project.delete.active') : '';
    return `
      <li class="proj-row${locked ? ' proj-general' : ''}" data-name="${esc(p.name)}">
        <span class="proj-name">${labelOf(p)}</span>
        <span class="proj-n">${t('project.memories', { n: fmtInt(p.memories) })}</span>
        <button class="btn btn-ghost btn-sm" data-act="rename"${locked ? ' disabled' : ''}
                aria-label="${esc(t('project.rename.aria', { name: p.name }))}">${t('project.rename')}</button>
        <button class="btn btn-danger btn-sm" data-act="delete"${locked ? ' hidden' : ''}
                ${p.active ? 'disabled' : ''} title="${esc(delTitle)}"
                aria-label="${esc(t('project.delete.aria', { name: p.name }))}">${t('project.delete')}</button>
      </li>`;
  };
  const m = openModal({
    title: t('project.manage.title'),
    bodyHTML: `<p class="hint">${t('project.manage.body')}</p>
               <ul class="proj-list">${data.projects.map(row).join('')}</ul>`,
    footHTML: `<button class="btn" data-x>${t('common.close')}</button>`,
  });
  m.querySelector('[data-x]').onclick = closeModal;
  m.querySelectorAll('.proj-row [data-act]').forEach(btn => {
    btn.onclick = async () => {
      const name = btn.closest('.proj-row').dataset.name;
      closeModal();
      if (btn.dataset.act === 'rename') await rename(name);
      else await remove(name, data.projects.find(p => p.name === name));
      manage();
    };
  });
}

async function rename(name) {
  const next = await promptModal({
    title: t('project.rename.title', { name: esc(name) }), body: t('project.rename.body'),
    label: t('project.create.label'), value: name, okLabel: t('project.rename.ok'),
  });
  if (next === null || next.trim() === name) return;
  try {
    const data = await client.projects.rename(name, { name: next.trim() });
    paint(data);
    toast(t('project.renamed', { from: esc(name), to: esc(data.name) }), 'ok');
  } catch (err) { failed('err.project', err); }
}

async function remove(name, project) {
  const n = project ? project.memories : 0;
  const ok = n
    ? await typedConfirmModal({
      title: t('project.delete.title', { name: esc(name) }),
      bodyHTML: t('project.delete.bodyFull', { name: esc(name), n: fmtInt(n) }),
      phrase: name, okLabel: t('project.delete.ok'),
    })
    : await confirmModal({
      title: t('project.delete.title', { name: esc(name) }),
      body: t('project.delete.body', { name: esc(name) }),
      okLabel: t('project.delete.ok'), danger: true,
    });
  if (!ok) return;
  try {
    const data = await client.projects.delete(name, { force: n > 0 });
    paint(data);
    toast(t('project.deleted', { name: esc(name) }), 'ok',
          data.backup ? { detail: t('project.deleted.backup', { name: data.backup }) } : {});
  } catch (err) { failed('err.project', err); }
}

/* ─── moving memories to another project, from a selection or a subtree: a dry run shows what
   moves and which cross-edge links the copy loses before the button is live. */

export async function moveToProjectModal({ uids = [], domain = '' }) {
  let data;
  try { data = await client.projects.list(); }
  catch (err) { failed('err.project', err); return false; }
  const others = data.projects.filter(p => p.name !== data.active);
  const targets = [...others.map(projectItem), newItem()];
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
            <input type="text" data-new placeholder="Acme" autocomplete="off" spellcheck="false"
                   aria-label="${t('project.create.label')}"${first === NEW ? '' : ' hidden'}>
          </div></div>
        <div class="mv-plan" data-plan><div>${scope}</div></div>`,
      footHTML: `<button class="btn" data-x>${t('common.cancel')}</button>
                 <button class="btn btn-solid" data-ok disabled>${t('mv.ok')}</button>`,
    });
    const okBtn = m.querySelector('[data-ok]');
    const plan = m.querySelector('[data-plan]');
    const input = m.querySelector('[data-new]');
    const creating = () => pickerValue(m, 'mvTarget') === NEW;
    const target = () => (creating() ? input.value.trim() : pickerValue(m, 'mvTarget'));
    const body = dry => ({ target: target(), uids, domain, dry_run: dry, create: creating() });

    const preview = async () => {
      const name = target();
      okBtn.disabled = true;
      if (!name) { plan.innerHTML = `<div>${scope}</div><div class="hint">${t('mv.nameIt')}</div>`; return; }
      try {
        const r = await client.projects.move(body(true));
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
        const r = await client.projects.move(body(false));
        toast(t('mv.done', { n: fmtInt(r.moved), target: esc(r.target) }), 'ok',
              r.backup ? { detail: t('mv.backup', { name: r.backup.split(/[\\/]/).pop() }) } : {});
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
