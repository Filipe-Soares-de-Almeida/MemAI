/* The "+ New memory" dialog. Not every type is written as one body: a diagram
   is created from a graph, so the modal asks for a title and seeds a
   start→end skeleton to grow on the canvas; a type made of named fields is
   created from those, and the server renders the body; a task is created from
   a goal and a list of items, through its own endpoint. Which types those are,
   and what they hold, comes from /api/config rather than from a list here, so
   a label lives in one place. */

import { esc } from '../core/dom.js';
import { api } from '../core/api.js';
import { toast, failed, openModal, closeModal } from '../core/ui.js';
import { typeItems, confItems, getDomains, invalidateDomains,
         domainDatalist, sectionLabelHTML } from '../core/shared.js';
import { pickerFor, pickerValue, wirePicker, fixedItems } from '../core/pick.js';
import { go, refreshBehind } from '../core/router.js';
import { openRecord } from './record.js';
import { newDiagramSkeleton } from './diagrams.js';
import { t } from '../i18n.js';

/* The types this form does not write: the server refuses a handoff here. */
const NOT_WRITTEN_HERE = ['handoff'];

/* The task limits the server enforces (memai/tasks.py), shown as counts. */
const GOAL_MAX = 2000;
const ITEMS_MAX = 50;
const ITEM_MAX = 300;

const itemLines = text => text.split(/\r?\n/).map(l => l.trim()).filter(Boolean);

export async function openNewMemory() {
  const domains = await getDomains().catch(() => []);
  const spec = await api('/api/config').then(c => c.sections || {}).catch(() => ({}));
  const types = typeItems().filter(it => !NOT_WRITTEN_HERE.includes(it.value));
  const confs = confItems();
  const modal = openModal({
    title: t('nm.title'),
    bodyHTML: `
      <div class="field-pair">
        <div class="field"><label for="nmType">${t('nm.type')}</label>
          ${pickerFor({ id: 'nmType', value: 'note', items: types, ariaLabel: t('nm.type') })}</div>
        <div class="field"><label for="nmConf">${t('nm.conf')}</label>
          ${pickerFor({ id: 'nmConf', value: 'unverified', items: confs, ariaLabel: t('nm.conf') })}</div>
      </div>
      <div class="field"><label for="nmTitle">${t('nm.name')}</label>
        <input type="text" id="nmTitle" placeholder="${t('nm.titlePh')}">
        <div class="dg-empty" id="nmDiagramHint" style="margin-top:7px" hidden>${t('nm.diagramHint')}</div></div>
      <div class="field"><label for="nmDomain">${t('nm.domain')}</label>
        <input type="text" id="nmDomain" list="nmDomainsDL" placeholder="${t('nm.domainPh')}">
        <datalist id="nmDomainsDL">${domainDatalist(domains)}</datalist></div>
      <div class="field"><label for="nmAlso">${t('nm.also')}</label>
        <input type="text" id="nmAlso" list="nmDomainsDL" placeholder="${t('mm.also.placeholder')}">
        <div class="hint-sm">${t('mm.also.hint')}</div></div>
      <div class="field"><label for="nmTags">${t('nm.tags')}</label>
        <input type="text" id="nmTags" placeholder="${t('nm.tagsPh')}"></div>
      <div class="field" id="nmContentField"><label for="nmContent">${t('nm.content')}</label>
        <textarea id="nmContent" rows="7" placeholder="${t('nm.contentPh')}"></textarea></div>
      <div id="nmSectionFields" hidden></div>
      <div id="nmTaskFields" hidden>
        <div class="field"><label for="nmGoal">${t('nm.task.goal')}
            <span class="sec-count" id="nmGoalCount"></span></label>
          <textarea id="nmGoal" class="tk-box" rows="3" placeholder="${t('nm.task.goalPh')}"></textarea></div>
        <div class="field"><label for="nmItems">${t('nm.task.items')}
            <span class="sec-count" id="nmItemsCount"></span></label>
          <textarea id="nmItems" class="tk-box" rows="6" placeholder="${t('nm.task.itemsPh')}"></textarea>
          <div class="hint-sm">${t('nm.task.itemsHint', { n: ITEMS_MAX, max: ITEM_MAX })}</div></div>
      </div>`,
    footHTML: `<button class="btn" data-x>${t('common.cancel')}</button><button class="btn btn-solid" data-ok>${t('nm.create')}</button>`,
  });
  const mq = s => modal.querySelector(s);

  const isDiagram = () => pickerValue(modal, 'nmType') === 'diagram';
  const isTask = () => pickerValue(modal, 'nmType') === 'task';
  const fieldsFor = () => spec[pickerValue(modal, 'nmType')] || [];
  const sync = () => {
    const type = pickerValue(modal, 'nmType');
    const fields = fieldsFor();
    mq('#nmContentField').hidden = isDiagram() || isTask() || fields.length > 0;
    mq('#nmTaskFields').hidden = !isTask();
    mq('#nmDiagramHint').hidden = !isDiagram();
    mq('#nmSectionFields').hidden = fields.length === 0;
    mq('#nmSectionFields').innerHTML = fields.map(f => `
      <div class="field"><label for="nmSec-${esc(f.key)}">${sectionLabelHTML(type, f)}
        ${f.max_len ? `<span class="sec-count" data-count="${esc(f.key)}"></span>` : ''}</label>
        <textarea id="nmSec-${esc(f.key)}" rows="4"></textarea></div>`).join('');
    /* the count is shown, never enforced by `maxlength`, which truncates a
       paste silently -- the server refuses the same body either way */
    fields.filter(f => f.max_len).forEach(f => {
      const box = mq(`#nmSec-${f.key}`);
      const out = mq(`[data-count="${f.key}"]`);
      const tick = () => {
        out.textContent = t('dr.sections.count', { n: box.value.length, max: f.max_len });
        out.classList.toggle('over', box.value.length > f.max_len);
      };
      box.addEventListener('input', tick);
      tick();
    });
    /* a diagram's content is its graph and a task is its checklist, so each
       is created unverified and the control says so by being unreachable
       rather than by being ignored */
    mq('#nmConf').disabled = isDiagram() || isTask();
  };

  /* counts are shown, never enforced by `maxlength`, for the reason above */
  const goalBox = mq('#nmGoal');
  const itemsBox = mq('#nmItems');
  const tickTask = () => {
    const goalOut = mq('#nmGoalCount');
    goalOut.textContent = t('dr.sections.count', { n: goalBox.value.length, max: GOAL_MAX });
    goalOut.classList.toggle('over', goalBox.value.length > GOAL_MAX);
    const lines = itemLines(itemsBox.value);
    const itemsOut = mq('#nmItemsCount');
    itemsOut.textContent = t('nm.task.itemsCount', { n: lines.length });
    itemsOut.classList.toggle('over',
      lines.length > ITEMS_MAX || lines.some(l => l.length > ITEM_MAX));
  };
  goalBox.addEventListener('input', tickTask);
  itemsBox.addEventListener('input', tickTask);
  tickTask();
  wirePicker(modal, { id: 'nmType', items: fixedItems(types), onPick: sync });
  wirePicker(modal, { id: 'nmConf', items: fixedItems(confs), onPick: () => {} });
  sync();

  mq('[data-x]').onclick = closeModal;
  mq('[data-ok]').onclick = async () => {
    try {
      if (isDiagram()) {
        const r = await newDiagramSkeleton({
          title: mq('#nmTitle').value,
          domain: mq('#nmDomain').value,
          also: mq('#nmAlso').value,
          tags: mq('#nmTags').value });
        closeModal();
        toast(t('nm.created', { uid: r.uid }), 'ok');
        invalidateDomains();
        go('diagram', { uid: r.uid });
        return;
      }
      if (isTask()) {
        const r = await api('/api/tasks', { body: {
          title: mq('#nmTitle').value, goal: goalBox.value, items: itemsBox.value,
          domain: mq('#nmDomain').value, also: mq('#nmAlso').value,
          tags: mq('#nmTags').value } });
        closeModal();
        toast(t('nm.task.created', { uid: r.uid }), 'ok');
        invalidateDomains();
        refreshBehind();
        openRecord(r.uid);
        return;
      }
      const fields = fieldsFor();
      const r = await api('/api/memories', { body: {
        type: pickerValue(modal, 'nmType'), confidence: pickerValue(modal, 'nmConf'),
        title: mq('#nmTitle').value,
        domain: mq('#nmDomain').value, also: mq('#nmAlso').value,
        tags: mq('#nmTags').value,
        ...(fields.length
          ? { sections: Object.fromEntries(
                fields.map(f => [f.key, mq(`#nmSec-${f.key}`).value])) }
          : { content: mq('#nmContent').value }) } });
      closeModal();
      toast(t('nm.created', { uid: r.uid }), 'ok');
      invalidateDomains();
      refreshBehind();
      openRecord(r.uid);
    } catch (err) { failed('err.create', err); }
  };
}
