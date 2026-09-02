/* The relations graph: the view around the node field.

   Everything drawn is in graph-engine.js and everything arranged is in
   graph-layout.js. This file owns the chrome -- the filters, the legend, the
   progress of the arrangement, the card, the tip and the link dialog -- and
   talks to the engine through its callbacks. The layout is computed in the
   browser and never stored, unlike the diagram editor, whose coordinates come
   from the store; see diagram.js for that contrast. */

import { $, esc, fmtInt, debounce } from '../core/dom.js';
import { api, query } from '../core/api.js';
import { icon } from '../core/icons.js';
import { toast, failed, tipShow, tipHide, openModal, closeModal, setPressed } from '../core/ui.js';
import { typeTag, typeColor, uidChip, statusTag, confPill, wireCopyChips,
         getDomains, TYPE_ORDER, TYPE_LABEL, typeItems, REL_SUGGEST, relTypeField,
         wireRelTypeField } from '../core/shared.js';
import { pickerFor, wirePicker, fixedItems } from '../core/pick.js';
import { domainPickerHTML, wireDomainPicker } from '../core/domain-picker.js';
import { go, refreshBehind } from '../core/router.js';
import { onTeardown } from '../core/lifecycle.js';
import { openRecord } from './record.js';
import { ForceGraph } from '../graph-engine.js';
import { t } from '../i18n.js';

/* How many of a memory's relations the card offers as a journey before it
   stops listing them, and how much of a name one of those chips carries. The
   card floats over the drawing, so what it costs is canvas -- and with the
   selection focused, its neighbours are named on the canvas itself, which is
   where a reader is already looking. */
const TRAVEL_MAX = 5;
const TRAVEL_CLIP = 16;

/* Everything that floats over the node field. The engine keeps the names out
   from under these, which it cannot work out for itself: where they sit is a
   stylesheet's decision. */
const CHROME = ['.graph-controls', '.graph-legend', '.graph-card',
                '.link-banner', '.graph-flight', '.graph-settle'];

export async function renderGraph(view, params, ctx) {
  const state = {
    status: params.has('status') ? params.get('status') : 'active',
    domain: params.get('domain') || '',
    type: params.get('type') || '',
  };
  const [domains, data] = await Promise.all([
    getDomains().catch(() => []),
    api(`/api/graph?${query(state)}`),
  ]);
  if (ctx.stale()) return;

  const counts = {};
  data.nodes.forEach(n => { counts[n.type] = (counts[n.type] || 0) + 1; });
  const types = typeItems({ any: t('common.allTypes') });

  /* A cut that says nothing is a cut that reads as "this is everything".
     The endpoint caps only when a `limit` asks it to, so this is the note
     for a narrowed request rather than a standing warning. */
  const capNote = data.truncated
    ? ` · <span style="color:var(--warn)">${t('g.truncated', { n: fmtInt(data.nodes.length), total: fmtInt(data.total) })}</span>`
    : '';

  view.innerHTML = `<div class="anim">
    <div class="view-head">
      <h2 class="view-title">${t('g.title')}</h2>
      <div class="view-sub">${t('g.sub', { n: fmtInt(data.nodes.length), m: fmtInt(data.edges.length) })}${capNote}</div>
    </div>
    <div class="graph-wrap" id="gWrap">
      <!-- A drawing, and labelled as one. The same records are in Memories as
           a list, which is what the label points at: an arrangement of forces
           has no reading order to expose, so pretending otherwise would be
           worse than saying where the text version lives. -->
      <canvas id="gGl" role="img"
              aria-label="${esc(t('g.canvasAlt', { n: fmtInt(data.nodes.length), m: fmtInt(data.edges.length) }))}"></canvas>
      <!-- the names, drawn over the node field: a handful of legible labels is
           a typography job and not a shader one -->
      <canvas id="gLabels" aria-hidden="true"></canvas>
      <div class="graph-controls">
        <!-- The spotlight, and it leads the row because it is the filter that
             answers the most questions. This view exists for the macro read --
             the shape of the store, where the clusters and the loose ends are
             -- so finding one memory must not destroy that shape: matching
             nodes keep their colour and size and everything else fades.
             Nothing is removed, no request is made, the arrangement never
             moves. Enter travels to the best match. -->
        <input type="search" id="gFind" class="graph-find" spellcheck="false" autocomplete="off"
               placeholder="${t('g.find')}" aria-label="${t('g.find')}">
        ${domainPickerHTML({ id: 'gDomain', value: state.domain, ariaLabel: t('common.allDomains') })}
        ${pickerFor({ id: 'gType', value: state.type, items: types, ariaLabel: t('common.allTypes') })}
        <div class="seg" role="group" aria-label="${t('mem.status.aria')}">
          <button type="button" data-v="active" aria-pressed="${state.status === 'active'}">${t('common.active')}</button>
          <button type="button" data-v="" aria-pressed="${state.status === ''}">${t('common.all')}</button>
        </div>
        <button type="button" class="btn btn-sm" id="gLink" aria-pressed="false">${icon('pencil')}${t('g.linkMode')}</button>
        <button type="button" class="btn btn-sm" id="gFly" aria-pressed="false"
                title="${esc(t('g.flightHint'))}">${icon('orbit')}${t('g.flight')}</button>
        <button type="button" class="btn btn-sm" id="gFit">${t('g.center')}</button>
        <!-- last, and pushed to the far end: a count that appeared between two
             controls would shove the whole row sideways on the first keystroke -->
        <span id="gFindCount" class="graph-find-count" aria-live="polite"></span>
      </div>
      <!-- The arrangement, while it is still arranging. It is a real wait on a
           large store and the graph visibly condenses through it, so the bar
           says how much of it is left rather than spinning. -->
      <div class="graph-settle" id="gSettle" hidden>
        <span>${t('g.settling', { n: fmtInt(data.nodes.length) })}</span>
        <span class="gs-track"><span class="gs-fill" id="gSettleFill"></span></span>
      </div>
      <div class="graph-legend">
        ${TYPE_ORDER.filter(tp => counts[tp]).map(tp =>
          `<span class="legend-item"><span class="dot" style="--c:${typeColor(tp)}"></span>${TYPE_LABEL[tp]} <b>${counts[tp]}</b></span>`).join('') || t('g.emptyLegend')}
        ${data.edges.length ? `<span class="legend-note">${t('g.dirNote')}</span>` : ''}
      </div>
      <!-- the keys, while they are the ones that do something -->
      <div class="graph-flight" id="gFlightHint" hidden>${esc(t('g.flightHint'))}</div>
      <div id="gBanner" class="link-banner" hidden></div>
      <div id="gCard" hidden></div>
    </div>
  </div>`;

  const nav = patch => {
    const p = { ...state, ...patch };
    const out = {};
    for (const [k, v] of Object.entries(p)) if (v) out[k] = v;
    if (p.status === '') out.status = '';
    if (p.status === 'active') delete out.status;
    go('graph', out);
  };
  wireDomainPicker(view, { id: 'gDomain', domains, onPick: domain => nav({ domain }) });
  wirePicker(view, { id: 'gType', items: fixedItems(types), onPick: type => nav({ type }) });
  view.querySelectorAll('.graph-controls .seg button').forEach(b =>
    b.addEventListener('click', () => nav({ status: b.dataset.v })));

  let engine;
  try {
    engine = new ForceGraph($('#gGl'), $('#gLabels'), {
      nodes: data.nodes,
      edges: data.edges,
      colorOf: typeColor,
      onSelect: node => renderCard(node),
      onOpen: node => openRecord(node.uid),
      onHover: (node, x, y) => {
        if (!node) { tipHide(); return; }
        tipShow(
          `<b>${esc(node.name)}</b><br>${esc(node.type)} · ${esc(node.uid)}`
          + `${node.domain ? `<br><span style="color:var(--ink-3)">${esc(node.domain)}</span>` : ''}`,
          x, y);
      },
      onLink: (kind, a, b) => {
        if (kind === 'from') {
          $('#gBanner').textContent = t('g.banner.target', { uid: a.uid });
        } else {
          promptLink(a, b);
        }
      },
      onSettle: (progress, done, err) => settle(progress, done, err),
      obstacles: () => {
        const frame = $('#gGl').getBoundingClientRect();
        return CHROME.flatMap(sel => {
          const el = $(sel);
          if (!el || el.hidden || !el.offsetParent) return [];
          const r = el.getBoundingClientRect();
          return [[r.left - frame.left, r.top - frame.top, r.width, r.height]];
        });
      },
      onFlight: on => {
        setPressed($('#gFly'), on);
        $('#gWrap').classList.toggle('flying', on);
        $('#gFlightHint').hidden = !on;
      },
    });
  } catch (err) {
    /* Nothing to fall back to and nothing to pretend. What this must NOT do
       is blame the browser for every failure: a shader that will not compile
       and a graphics card that will not give up a context both arrive here,
       and only one of them is the reader's to do anything about. So the
       browser's own words go on screen, and the retry is here because a
       refused context is often a passing state. */
    console.error('relations graph:', err);
    const wrap = $('#gWrap');
    wrap.innerHTML = `<div class="graph-blocked" role="alert">
      <p>${esc(t('g.startFailed'))}${err?.kind === 'context' ? ` ${esc(t('g.webgl'))}` : ''}</p>
      ${err?.message ? `<p class="failed-detail">${esc(err.message)}</p>` : ''}
      <div class="act-row">
        <button type="button" class="btn btn-solid" id="gRetry">${t('common.retry')}</button>
        <button type="button" class="btn" id="gToList">${t('nav.memories')}</button>
      </div>
    </div>`;
    $('#gRetry').addEventListener('click', () => refreshBehind());
    $('#gToList').addEventListener('click', () => go('memories'));
    return;
  }
  /* the canvases go with the view's innerHTML; the window listeners, the
     animation frame and the worker do not */
  onTeardown(() => engine.destroy());

  $('#gFit').addEventListener('click', () => engine.fit());
  $('#gLink').addEventListener('click', () => {
    const on = engine.toggleLinkMode();
    const banner = $('#gBanner');
    banner.hidden = !on;
    if (on) banner.textContent = t('g.banner.source');
    setPressed($('#gLink'), on);
  });
  $('#gFly').addEventListener('click', () => engine.setFlight(!engine.flight));

  function settle(progress, done, err) {
    const bar = $('#gSettle');
    if (!bar) return;
    if (err) {
      bar.hidden = true;
      toast(t('g.err', { msg: err }), 'bad');
      return;
    }
    bar.hidden = done;
    $('#gSettleFill').style.transform = `scaleX(${Math.min(1, progress).toFixed(3)})`;
  }

  /* Every term has to match, the same as the diagram list's filter -- one
     boolean for every client-side filter in the app rather than one each. */
  const find = $('#gFind');
  let match = null;
  const spot = () => {
    const hit = engine.spotlight(find.value);
    match = hit.first;
    $('#gFindCount').textContent = find.value.trim()
      ? t('g.findCount', { n: fmtInt(hit.count), total: fmtInt(engine.nodes.length) }) : '';
  };
  find.addEventListener('input', debounce(spot, 160));
  find.addEventListener('keydown', e => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    spot();
    if (match) engine.select(match.uid);
  });

  function renderCard(node) {
    const card = $('#gCard');
    if (!card) return;
    if (!node) { card.hidden = true; card.innerHTML = ''; return; }
    card.className = 'graph-card';
    card.hidden = false;
    const peers = engine.neighbours(node.uid);
    const shown = peers.slice(0, TRAVEL_MAX);
    /* the opening line of the body, but only when it is not already the name:
       an untitled memory is named BY that line, and printing it twice says
       nothing the second time */
    const body = node.label && node.label !== node.name ? node.label : '';
    const also = (node.also || []).length;
    card.innerHTML = `
      <div class="gc-head">
        <div class="gc-tags">
          ${typeTag(node.type)} ${uidChip(node.uid)} ${statusTag(node.status)} ${confPill(node.confidence)}
        </div>
        <!-- Clicking empty space also clears the selection, but nothing on
             screen says so, and the selection is what is dimming the rest of
             the store. -->
        <button type="button" class="icon-btn" data-shut
                aria-label="${t('common.close')}" title="${t('common.close')}">${
          icon('close', { title: t('common.close') })}</button>
      </div>
      <div class="gc-name">${esc(node.name)}</div>
      ${body ? `<div class="snippet">${esc(body)}</div>` : ''}
      <div class="act-row">
        <button class="btn btn-sm btn-solid" data-openrec>${t('common.openRecord')}</button>
        ${node.domain ? `<span class="chip">${esc(node.domain)}</span>` : ''}
        <!-- the paths it is cross-listed into, counted rather than listed: a
             memory in four subjects was four chips and two more lines -->
        ${also ? `<span class="chip" title="${esc((node.also || []).join(' · '))}">${
          t('g.alsoIn', { n: also })}</span>` : ''}
      </div>
      ${shown.length ? `<div class="gc-travel">
        <span class="gc-travel-head">${t('g.travel')} <span class="gc-walk">${t('g.walk')}</span></span>
        <div class="gc-hops">
          ${shown.map(p => `<button type="button" class="gc-hop" data-hop="${esc(p.uid)}"
                  title="${esc(p.name)}"><span class="dot" style="--c:${typeColor(p.type)}"></span>${
            esc(clip(p.name, TRAVEL_CLIP))}</button>`).join('')}
          ${peers.length > shown.length
            ? `<span class="chip">${t('g.travelMore', { n: peers.length - shown.length })}</span>` : ''}
        </div>
      </div>` : `<div class="gc-travel"><span class="gc-travel-head">${t('g.noLinks')}</span></div>`}`;
    wireCopyChips(card);
    card.querySelector('[data-openrec]').addEventListener('click', () => openRecord(node.uid));
    card.querySelector('[data-shut]').addEventListener('click', () => engine.select(null));
    card.querySelectorAll('[data-hop]').forEach(b =>
      b.addEventListener('click', () => engine.select(b.dataset.hop)));
  }

  function promptLink(a, b) {
    const modal = openModal({
      title: t('g.modal.title'),
      bodyHTML: `
        <div class="gl-peers">
          <div><span class="dot" style="--c:${typeColor(a.type)};display:inline-block;margin-right:6px"></span>${esc(a.uid)} · ${esc(a.name)}</div>
          <div style="color:var(--accent);padding-left:2px;--ico:15px">${icon('arrow-down')}</div>
          <div><span class="dot" style="--c:${typeColor(b.type)};display:inline-block;margin-right:6px"></span>${esc(b.uid)} · ${esc(b.name)}</div>
        </div>
        <div class="field"><label for="glType">${t('g.modal.relType')}</label>
          <div class="act-row">${relTypeField({
            selId: 'glType', customId: 'glTypeCustom', options: REL_SUGGEST,
            value: 'relates_to', ariaLabel: t('g.modal.relType') })}</div></div>
        <div class="field"><label for="glNote">${t('g.modal.note')}</label><input type="text" id="glNote"></div>`,
      footHTML: `<button class="btn" data-x>${t('common.cancel')}</button><button class="btn btn-solid" data-ok>${t('g.modal.create')}</button>`,
    });
    const mq = s => modal.querySelector(s);
    const glRelValue = wireRelTypeField(modal, {
      selId: 'glType', customId: 'glTypeCustom', options: REL_SUGGEST });
    mq('[data-x]').onclick = () => { closeModal(); engine.clearLinkFrom(); };
    mq('[data-ok]').onclick = async () => {
      try {
        await api('/api/relations', { body: {
          from_uid: a.uid, to_uid: b.uid,
          relation_type: glRelValue() || 'relates_to',
          note: mq('#glNote').value } });
        closeModal();
        toast(t('dr.rel.created'), 'ok');
        refreshBehind();
      } catch (err) { failed('err.relation', err); }
    };
  }
}

const clip = (text, max) => {
  const s = String(text || '');
  return s.length > max ? `${s.slice(0, max - 1).trimEnd()}…` : s;
};
