import { describe, expect, it } from 'vitest';
import { nextTick } from 'vue';
import { openNewMemory } from '../../src/memai/webui/views/new-memory/index.ts';
import { closeModal } from '../../src/memai/webui/core/modal-stack.ts';
import { MEMORY } from '../../src/memai/webui/contract.ts';
import { calls, catalog, serveApi } from './support.js';

const en = catalog('en');

/* a sectioned type as /api/config describes it; the dialog reads its fields from here */
const SPEC = {
  anti_pattern: [
    { key: 'pattern', label: 'TEMPTATION', max_len: 12 },
    { key: 'why_wrong', label: 'WHY WRONG', max_len: 0 },
  ],
};

type Call = { method: string; body: unknown };
type Handler = (path: string, call: Call) => unknown;

const base: Handler = path => {
  if (path === '/api/domains') return { domains: [{ domain: 'lantern/wick' }, { domain: 'kiln' }] };
  if (path === '/api/config') return { sections: SPEC };
  throw new Error(`unexpected ${path}`);
};

async function until<T>(check: () => T, ms = 2000): Promise<T> {
  const end = Date.now() + ms;
  for (;;) {
    const got = check();
    if (got) return got;
    if (Date.now() > end) throw new Error('condition never held');
    await new Promise(done => setTimeout(done, 5));
  }
}

const $ = <E extends HTMLElement = HTMLElement>(sel: string) => document.querySelector(sel) as E;

async function fill(id: string, text: string) {
  const box = $<HTMLInputElement | HTMLTextAreaElement>(`#${id}`);
  box.value = text;
  box.dispatchEvent(new Event('input'));
  await nextTick();
}

async function pick(type: string) {
  $('#nmType').click();
  $(`.pick-pop [role="option"][data-v="${type}"]`).click();
  await until(() => $('#nmType').dataset.v === type);
}

async function open(answer: Handler = () => undefined) {
  serveApi((path: string, call: Call) => answer(path, call) ?? base(path, call));
  await openNewMemory();
  return $('.modal');
}

const shut = () => { while (document.querySelector('.modal')) closeModal(); };

describe('the new memory dialog', () => {
  it('offers a task but not a handoff, and the known domains to complete from', async () => {
    await open();
    $('#nmType').click();
    const offered = [...document.querySelectorAll<HTMLElement>('.pick-pop [role="option"]')].map(o => o.dataset.v);
    expect(offered).toContain('task');
    expect(offered).toContain('note');
    expect(offered).not.toContain('handoff');
    $('#nmType').click();
    const options = [...document.querySelectorAll<HTMLOptionElement>('#nmDomainsDL option')].map(o => o.value);
    expect(options).toEqual(['kiln', 'lantern/wick']);
    shut();
  });

  it('writes a plain type as one body, with its confidence', async () => {
    await open((path, call) =>
      (path === '/api/memories' && call.method === 'POST' ? { uid: 'feedc0de00000002' } : undefined));
    await fill('nmTitle', 'Wick trim interval');
    await fill('nmContent', 'The wick trims itself every forty hours.');
    $('#nmConf').click();
    $('.pick-pop [role="option"][data-v="confirmed"]').click();
    $('[data-ok]').click();
    const sent = await until(() => calls.find(c => c.path === '/api/memories'));
    expect(sent.body).toEqual({ type: 'note', confidence: 'confirmed', title: 'Wick trim interval',
                                domain: '', also: '', tags: '', content: 'The wick trims itself every forty hours.' });
    await until(() => !document.querySelector('.modal'));
    expect(location.hash).toBe('#/memory?uid=feedc0de00000002');
  });

  it("swaps the body for a sectioned type's own fields, counted against their limits", async () => {
    await open((path, call) =>
      (path === '/api/memories' && call.method === 'POST' ? { uid: 'feedc0de00000003' } : undefined));
    await pick('anti_pattern');
    expect($('#nmContentField').hidden).toBe(true);
    expect($('#nmSectionFields').hidden).toBe(false);
    expect([...document.querySelectorAll('#nmSectionFields textarea')].map(el => el.id))
      .toEqual(['nmSec-pattern', 'nmSec-why_wrong']);
    await fill('nmSec-pattern', 'Oiling a turning gear');
    const counter = $('#nmSectionFields .sec-count');
    expect(counter.classList.contains('over')).toBe(true);
    expect(counter.textContent).toBe(en['dr.sections.count'].replace('{n}', '21').replace('{max}', '12'));
    expect(document.querySelectorAll('#nmSectionFields .sec-count')).toHaveLength(1);
    await fill('nmSec-why_wrong', 'The oil flings off.');
    $('[data-ok]').click();
    const sent = await until(() => calls.find(c => c.path === '/api/memories'));
    expect(sent.body).toMatchObject({ type: 'anti_pattern',
      sections: { pattern: 'Oiling a turning gear', why_wrong: 'The oil flings off.' } });
    expect(sent.body).not.toHaveProperty('content');
  });

  it('counts the domain and the tags against their ceilings', async () => {
    await open();
    await fill('nmDomain', '  kiln  ');
    expect($('#nmDomainCount').textContent).toBe(`4/${MEMORY.DOMAIN_MAX}`);
    expect($('#nmDomainCount').classList.contains('over')).toBe(false);
    await fill('nmTags', 'k'.repeat(MEMORY.TAGS_MAX + 1));
    expect($('#nmTagsCount').classList.contains('over')).toBe(true);
    shut();
  });

  it('posts a task to its own endpoint, with its goal and items', async () => {
    await open(path => (path === '/api/tasks' ? { uid: 'feedc0de00000001' } : undefined));
    await pick('task');
    expect($('#nmTaskFields').hidden).toBe(false);
    expect($('#nmContentField').hidden).toBe(true);
    expect($<HTMLButtonElement>('#nmConf').disabled).toBe(true);

    await fill('nmTitle', 'Lantern firmware');
    await fill('nmGoal', 'Ship the lantern firmware');
    await fill('nmItems', 'Solder the header\n\n  Flash the board  ');
    await fill('nmDomain', 'lantern/firmware');
    expect($('#nmItemsCount').textContent).toBe('2 items');
    $('[data-ok]').click();

    const sent = await until(() => calls.find(c => c.path === '/api/tasks'));
    expect(sent.method).toBe('POST');
    expect(sent.body).toEqual({
      title: 'Lantern firmware', goal: 'Ship the lantern firmware',
      items: 'Solder the header\n\n  Flash the board  ', domain: 'lantern/firmware', also: '', tags: '',
    });
    expect(calls.some(c => c.path === '/api/memories' && c.method === 'POST')).toBe(false);
  });

  it('seeds a diagram as a start-to-end skeleton and opens it on the canvas', async () => {
    await open(path => (path === '/api/diagrams' ? { uid: 'feedc0de00000004', also: [] } : undefined));
    await pick('diagram');
    expect($('#nmDiagramHint').hidden).toBe(false);
    expect($('#nmContentField').hidden).toBe(true);
    expect($<HTMLButtonElement>('#nmConf').disabled).toBe(true);
    await fill('nmTitle', 'Nightly kiln cycle');
    $('[data-ok]').click();
    const sent = await until(() => calls.find(c => c.path === '/api/diagrams'));
    expect(sent.body).toMatchObject({ title: 'Nightly kiln cycle', edges: [{ from: 'start', to: 'finish' }] });
    await until(() => location.hash === '#/diagram?uid=feedc0de00000004');
  });

  it('keeps the dialog and what was typed when the server refuses it', async () => {
    await open(path => {
      if (path === '/api/memories') throw new Error('a title is one line');
      return undefined;
    });
    await fill('nmTitle', 'Lantern wick');
    $('[data-ok]').click();
    await until(() => document.getElementById('toasts')?.textContent?.includes('a title is one line'));
    expect($('.modal')).not.toBeNull();
    expect($<HTMLInputElement>('#nmTitle').value).toBe('Lantern wick');
    shut();
  });
});
