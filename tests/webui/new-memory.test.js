import { describe, expect, it } from 'vitest';
import { openNewMemory } from '../../src/memai/webui/views/new-memory.js';
import { calls, serveApi } from './support.js';

async function until(check, ms = 2000) {
  const end = Date.now() + ms;
  for (;;) {
    const got = check();
    if (got) return got;
    if (Date.now() > end) throw new Error('condition never held');
    await new Promise(done => setTimeout(done, 5));
  }
}

/* what the dialog reads before it draws: the domain list and the type config */
const base = path => (path === '/api/domains' ? { domains: [] } : {});

const typeOptions = () => [...document.querySelectorAll('.pick-pop [role="option"]')].map(o => o.dataset.v);

describe('the new memory dialog', () => {
  it('offers a task but not a handoff', async () => {
    serveApi(base);
    await openNewMemory();
    document.getElementById('nmType').click();
    const offered = typeOptions();
    expect(offered).toContain('task');
    expect(offered).toContain('note');
    expect(offered).not.toContain('handoff');
  });

  it('posts a task to its own endpoint, with its goal and items', async () => {
    serveApi(path => (path === '/api/tasks' ? { uid: 'feedc0de00000001' } : base(path)));
    await openNewMemory();
    document.getElementById('nmType').click();
    document.querySelector('.pick-pop [role="option"][data-v="task"]').click();
    expect(document.getElementById('nmTaskFields').hidden).toBe(false);
    expect(document.getElementById('nmContentField').hidden).toBe(true);

    document.getElementById('nmTitle').value = 'Lantern firmware';
    document.getElementById('nmGoal').value = 'Ship the lantern firmware';
    document.getElementById('nmItems').value = 'Solder the header\nFlash the board';
    document.getElementById('nmDomain').value = 'lantern/firmware';
    document.querySelector('[data-ok]').click();

    const sent = await until(() => calls.find(c => c.path === '/api/tasks'));
    expect(sent.method).toBe('POST');
    expect(sent.body).toMatchObject({
      title: 'Lantern firmware', goal: 'Ship the lantern firmware',
      items: 'Solder the header\nFlash the board', domain: 'lantern/firmware',
    });
    expect(calls.some(c => c.path === '/api/memories' && c.method === 'POST')).toBe(false);
  });
});
