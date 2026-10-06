/* The project switch in the app bar and the move-to-project dialog. A switch re-runs the route,
   which re-reads the new project; it is refused while a dialog is open. */

import { $, esc, fmtInt, debounce } from './dom.ts';
import { toast, failed, modalOpen, openModal, closeModal, promptModal } from './ui.js';
import { pickerFor, pickerValue, setPickerValue, wirePicker, fixedItems } from './pick.js';
import { route } from './router.ts';
import { t } from '../i18n.ts';
import * as client from '../api/client.ts';

/* The row that is an action rather than a project. A project name is a file
   name, and no file name may carry this character. */
const NEW = '*new';

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
const items = () => [...rows.map(projectItem), newItem()];

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
