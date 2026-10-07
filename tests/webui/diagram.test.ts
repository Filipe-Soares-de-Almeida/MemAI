import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { nextTick } from 'vue';
import DiagramView from '../../src/memai/webui/views/diagram/DiagramView.vue';
import { backHref, chipLabel, jumpHref, navOffers } from '../../src/memai/webui/views/diagram/diagram.ts';
import { DiagramEditor } from '../../src/memai/webui/engines/diagram-engine.ts';
import { mountView } from '../../src/memai/webui/core/vue.ts';
import { teardownView } from '../../src/memai/webui/core/lifecycle.ts';
import { calls, catalog, serveApi } from './support.js';

const en = catalog('en');
const settle = async () => { for (let i = 0; i < 4; i++) { await nextTick(); await new Promise(r => setTimeout(r, 0)); } };

/* happy-dom draws nothing; a context that answers every call lets the engine start */
const stubCtx = () => new Proxy({} as Record<string | symbol, unknown>, {
  get: (o, k) => (k in o ? o[k] : k === 'measureText' ? (s: string) => ({ width: String(s).length * 6 }) : () => {}),
  set: (o, k, v) => { o[k] = v; return true; },
});
HTMLCanvasElement.prototype.getContext = function () { return stubCtx(); } as never;

type Hooks = { onSelect: (n: unknown) => void; onMove: (p: unknown) => void; onConnect: (a: string, b: string) => void };
let engine: { byKey: Record<string, unknown>; hooks: Hooks } | undefined;
const setData = DiagramEditor.prototype.setData;
beforeEach(() => {
  DiagramEditor.prototype.setData = function (this: DiagramEditor, ...a: Parameters<DiagramEditor['setData']>) {
    engine = this as never;
    return setData.apply(this, a);
  };
});
afterEach(() => { DiagramEditor.prototype.setData = setData; teardownView(); });

const node = (key: string, shape: string, label: string, extra = {}) =>
  ({ key, label, shape, note: '', seq: 0, x: 100, y: 100, w: null, h: null, ...extra });
const jump = (extra: Record<string, unknown>) => ({ direction: 'out', node_key: 'b', peer_uid: 'p1', peer_node: '',
  peer_title: 'Wick routine', peer_node_label: '', peer_status: 'active', label: '', created_at: '', ...extra });
const flow = (label = 'Pour oil', extra = {}) => ({
  uid: 'f1', kind: 'flowchart', title: 'Refill routine', summary: '', font_scale: 1, mermaid: '',
  nodes: [node('a', 'start', 'Start'), node('b', 'step', label), node('c', 'end', 'Done')],
  edges: [{ from: 'a', to: 'b', label: '', seq: 0, loops: false }, { from: 'b', to: 'c', label: '', seq: 1, loops: false }],
  links: [], jumps: [], ...extra });

async function show(params: Record<string, string>, answer: (path: string, call: { method: string }) => unknown) {
  serveApi(answer);
  const view = document.getElementById('view') as HTMLElement;
  await mountView(DiagramView, view, new URLSearchParams(params), { stale: () => false });
  await settle();
  return view;
}
const posts = (path: string) => calls.filter(c => c.method === 'POST' && c.path === path);
const answerModal = async (sel: '[data-ok]' | '[data-x]') => {
  (document.querySelector(`.modal ${sel}`) as HTMLElement).click();
  await settle();
};

describe('the jump addresses', () => {
  it('lead to the step at the far end and say where they left from; a return carries no way back', () => {
    expect(jumpHref(jump({ peer_node: 's', node_key: 'b' }) as never, 'f1')).toBe('#/diagram?uid=p1&node=s&from=f1&fromNode=b');
    expect(jumpHref(jump({ node_key: '' }) as never, 'f1')).toBe('#/diagram?uid=p1&from=f1');
    expect(backHref('p1', '')).toBe('#/diagram?uid=p1');
  });

  it('cut a flow name at its separator and clamp it for a chip', () => {
    expect(chipLabel('R-17 — refill every lamp before dusk')).toBe('R-17');
    expect(chipLabel('A very long routine name with no separator')).toBe('A very long routine…');
  });

  it('offer the way back first and not again as a way on', () => {
    const data = flow('Pour oil', { jumps: [jump({ peer_uid: 'p2', direction: 'in' }), jump({ peer_uid: 'p3' }),
                                             jump({ peer_uid: 'p4', node_key: '' })] });
    const offers = navOffers(data as never, 'f1', 'b', { from: 'p2', fromNode: '' });
    expect(offers.map(o => [o.kind, o.title === 'Wick routine' && o.href])).toEqual([
      ['back', '#/diagram?uid=p2'], ['go', '#/diagram?uid=p3&from=f1&fromNode=b'], ['go', '#/diagram?uid=p4&from=f1']]);
  });
});

describe('the diagram editor', () => {
  it('says so when the address names no diagram, or a memory that is not one', async () => {
    let view = await show({}, () => ({}));
    expect(view.textContent).toBe(en['dg.noUid']);
    teardownView();
    view = await show({ uid: 'n1' }, () => ({ uid: 'n1', type: 'note', diagram: null }));
    expect(view.textContent).toBe(en['dg.notDiagram']);
  });

  it('opens read-only, and editing unlocks the tools and the title', async () => {
    const view = await show({ uid: 'f1' }, () => ({ uid: 'f1', type: 'diagram', diagram: flow() }));
    expect([...view.querySelectorAll<HTMLButtonElement>('[data-editonly]')].every(b => b.disabled)).toBe(true);
    (view.querySelector('#dgTitle') as HTMLElement).click();
    await settle();
    expect(document.querySelector('.modal')).toBeNull();
    (view.querySelector('#dgMode') as HTMLElement).click();
    await settle();
    expect(view.querySelector('#dgMode')?.getAttribute('aria-pressed')).toBe('true');
    expect([...view.querySelectorAll<HTMLButtonElement>('[data-editonly]')].some(b => b.disabled)).toBe(false);
    expect(view.querySelector('#dgTitle')?.classList.contains('dg-editable')).toBe(true);
  });

  it('writes a step through the API and draws the flow the store sends back', async () => {
    let label = 'Pour oil';
    const view = await show({ uid: 'f1' }, (path, call) => {
      if (path === '/api/diagrams/f1/node' && call.method === 'POST') { label = 'Pour grade B oil'; return { ok: true, key: 'b' }; }
      if (path === '/api/diagrams/f1') return flow(label);
      return { uid: 'f1', type: 'diagram', diagram: flow(label) };
    });
    (view.querySelector('#dgMode') as HTMLElement).click();
    engine?.hooks.onSelect(engine.byKey.b);
    await settle();
    const field = view.querySelector('#dgLabel') as HTMLInputElement;
    field.value = 'Pour grade B oil';
    field.dispatchEvent(new Event('input'));
    (view.querySelector('#dgSave') as HTMLElement).click();
    await settle();
    expect(posts('/api/diagrams/f1/node')[0].body).toEqual({ key: 'b', label: 'Pour grade B oil', shape: 'step', note: '' });
    expect(calls.some(c => c.method === 'GET' && c.path === '/api/diagrams/f1')).toBe(true);
    expect((view.querySelector('#dgLabel') as HTMLInputElement).value).toBe('Pour grade B oil');
  });

  it('asks before deleting a step, and a cancel writes nothing', async () => {
    const view = await show({ uid: 'f1' }, path => path === '/api/diagrams/f1' ? flow()
      : path.startsWith('/api/diagrams/') ? { ok: true } : { uid: 'f1', type: 'diagram', diagram: flow() });
    (view.querySelector('#dgMode') as HTMLElement).click();
    engine?.hooks.onSelect(engine.byKey.b);
    await settle();
    (view.querySelector('#dgDel') as HTMLElement).click();
    await settle();
    expect(document.querySelector('.modal [data-ok]')?.classList.contains('btn-danger')).toBe(true);
    await answerModal('[data-x]');
    expect(posts('/api/diagrams/f1/node')).toHaveLength(0);
    (view.querySelector('#dgDel') as HTMLElement).click();
    await settle();
    await answerModal('[data-ok]');
    expect(posts('/api/diagrams/f1/node')[0].body).toEqual({ key: 'b', delete: true });
  });

  it('labels a new connection and writes it', async () => {
    await show({ uid: 'f1' }, path => path === '/api/diagrams/f1' ? flow()
      : path.startsWith('/api/diagrams/') ? { ok: true } : { uid: 'f1', type: 'diagram', diagram: flow() });
    engine?.hooks.onConnect('a', 'c');
    await settle();
    const field = document.querySelector('.modal [data-in]') as HTMLInputElement;
    field.value = 'skip';
    field.dispatchEvent(new Event('input'));
    await answerModal('[data-ok]');
    expect(posts('/api/diagrams/f1/edge')[0].body).toEqual({ from: 'a', to: 'c', label: 'skip' });
  });

  it('writes moved cards in one batch, and still writes one pending when the view goes', async () => {
    await show({ uid: 'f1' }, path => path === '/api/diagrams/f1/layout' ? { ok: true }
      : { uid: 'f1', type: 'diagram', diagram: flow() });
    engine?.hooks.onMove({ a: { x: 10, y: 20 } });
    engine?.hooks.onMove({ b: { x: 30, y: 40 } });
    expect(posts('/api/diagrams/f1/layout')).toHaveLength(0);
    teardownView();
    await settle();
    expect(posts('/api/diagrams/f1/layout').map(c => c.body))
      .toEqual([{ positions: { a: { x: 10, y: 20 }, b: { x: 30, y: 40 } } }]);
  });

  it('arrives on the step a jump named, with the way back in the corner, and folds past three', async () => {
    const jumps = [jump({ peer_uid: 'p2', direction: 'in', peer_node: 's' }), jump({ peer_uid: 'p3' }),
                   jump({ peer_uid: 'p4' }), jump({ peer_uid: 'p5' })];
    const view = await show({ uid: 'f1', node: 'b', from: 'p2', fromNode: 's' },
                            () => ({ uid: 'f1', type: 'diagram', diagram: flow('Pour oil', { jumps }) }));
    expect(view.querySelector('#dgLabel')).not.toBeNull();
    const chips = [...view.querySelectorAll('#dgJumpNav .dg-navchip:not(.dg-navmore)')];
    expect(chips[0].classList.contains('back')).toBe(true);
    expect(chips[0].getAttribute('href')).toBe('#/diagram?uid=p2&node=s');
    const more = view.querySelector('.dg-navmore') as HTMLElement;
    expect(more.textContent).toBe('+1');
    more.click();
    await settle();
    expect(view.querySelector('#dgJumpNav')?.classList.contains('open')).toBe(true);
    expect(more.textContent).toBe('−');
  });
});
