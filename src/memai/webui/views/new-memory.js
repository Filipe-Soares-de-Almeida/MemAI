/* The "+ New memory" dialog. A diagram is the odd one out: it is created
   from a graph rather than from content, so the modal asks for a title and
   seeds a start→end skeleton to grow on the canvas. */

import { esc } from '../core/dom.js';
import { api } from '../core/api.js';
import { toast, failed, openModal, closeModal } from '../core/ui.js';
import { typeItems, confItems, getDomains, invalidateDomains,
         domainDatalist } from '../core/shared.js';
import { pickerFor, pickerValue, wirePicker, fixedItems } from '../core/pick.js';
import { go, refreshBehind } from '../core/router.js';
import { openRecord } from './record.js';
import { newDiagramSkeleton } from './diagrams.js';
import { t } from '../i18n.js';

export async function openNewMemory() {
  const domains = await getDomains().catch(() => []);
  const types = typeItems();
  const confs = confItems();
  const modal = openModal({
    title: t('nm.title'),
    bodyHTML: `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <div class="field"><label for="nmType">${t('nm.type')}</label>
          ${pickerFor({ id: 'nmType', value: 'note', items: types, ariaLabel: t('nm.type') })}</div>
        <div class="field"><label for="nmConf">${t('nm.conf')}</label>
          ${pickerFor({ id: 'nmConf', value: 'unverified', items: confs, ariaLabel: t('nm.conf') })}</div>
      </div>
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
      <div class="field" id="nmTitleField" hidden><label for="nmTitle">${t('dg.meta.name')}</label>
        <input type="text" id="nmTitle" placeholder="${t('nm.titlePh')}">
        <div class="dg-empty" style="margin-top:7px">${t('nm.diagramHint')}</div></div>`,
    footHTML: `<button class="btn" data-x>${t('common.cancel')}</button><button class="btn btn-solid" data-ok>${t('nm.create')}</button>`,
  });
  const mq = s => modal.querySelector(s);

  const isDiagram = () => pickerValue(modal, 'nmType') === 'diagram';
  const sync = () => {
    mq('#nmContentField').hidden = isDiagram();
    mq('#nmTitleField').hidden = !isDiagram();
    /* a diagram's content is its graph, so it is created unverified and the
       control says so by being unreachable rather than by being ignored */
    mq('#nmConf').disabled = isDiagram();
  };
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
      const r = await api('/api/memories', { body: {
        type: pickerValue(modal, 'nmType'), confidence: pickerValue(modal, 'nmConf'),
        domain: mq('#nmDomain').value, also: mq('#nmAlso').value,
        tags: mq('#nmTags').value, content: mq('#nmContent').value } });
      closeModal();
      toast(t('nm.created', { uid: r.uid }), 'ok');
      invalidateDomains();
      refreshBehind();
      openRecord(r.uid);
    } catch (err) { failed('err.create', err); }
  };
}
