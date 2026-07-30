/* The "+ New memory" dialog. A diagram is the odd one out: it is created
   from a graph rather than from content, so the modal asks for a title and
   seeds a start→end skeleton to grow on the canvas. */

import { esc } from '../core/dom.js';
import { api } from '../core/api.js';
import { toast, failed, openModal, closeModal } from '../core/ui.js';
import { TYPE_ORDER, CONF, getDomains, invalidateDomains } from '../core/shared.js';
import { go, refreshBehind } from '../core/router.js';
import { openRecord } from './record.js';
import { newDiagramSkeleton } from './diagrams.js';
import { t } from '../i18n.js';

export async function openNewMemory() {
  const domains = await getDomains().catch(() => []);
  const modal = openModal({
    title: t('nm.title'),
    bodyHTML: `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <div class="field"><label for="nmType">${t('nm.type')}</label>
          <select id="nmType">${TYPE_ORDER.map(tp => `<option ${tp === 'note' ? 'selected' : ''}>${tp}</option>`).join('')}</select></div>
        <div class="field"><label for="nmConf">${t('nm.conf')}</label>
          <select id="nmConf">${Object.keys(CONF).map(c => `<option value="${c}">${CONF[c].label}</option>`).join('')}</select></div>
      </div>
      <div class="field"><label for="nmDomain">${t('nm.domain')}</label>
        <input type="text" id="nmDomain" list="nmDomainsDL" placeholder="${t('nm.domainPh')}">
        <datalist id="nmDomainsDL">${domains.map(d => `<option value="${esc(d.domain)}">`).join('')}</datalist></div>
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

  const isDiagram = () => mq('#nmType').value === 'diagram';
  const sync = () => {
    mq('#nmContentField').hidden = isDiagram();
    mq('#nmTitleField').hidden = !isDiagram();
    mq('#nmConf').disabled = isDiagram();
  };
  mq('#nmType').addEventListener('change', sync);
  sync();

  mq('[data-x]').onclick = closeModal;
  mq('[data-ok]').onclick = async () => {
    try {
      if (isDiagram()) {
        const r = await newDiagramSkeleton({
          title: mq('#nmTitle').value,
          domain: mq('#nmDomain').value,
          tags: mq('#nmTags').value });
        closeModal();
        toast(t('nm.created', { uid: r.uid }), 'ok');
        invalidateDomains();
        go('diagram', { uid: r.uid });
        return;
      }
      const r = await api('/api/memories', { body: {
        type: mq('#nmType').value, confidence: mq('#nmConf').value,
        domain: mq('#nmDomain').value, tags: mq('#nmTags').value,
        content: mq('#nmContent').value } });
      closeModal();
      toast(t('nm.created', { uid: r.uid }), 'ok');
      invalidateDomains();
      refreshBehind();
      openRecord(r.uid);
    } catch (err) { failed('err.create', err); }
  };
}
