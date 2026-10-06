import { describe, expect, it } from 'vitest';
import ChangelogView from '../../src/memai/webui/views/changelog/ChangelogView.vue';
import { mountView } from '../../src/memai/webui/core/vue.ts';
import { teardownView } from '../../src/memai/webui/core/lifecycle.ts';
import { calls, catalog, serveApi } from './support.js';

const en = catalog('en');

const updateState = (over = {}) => ({
  current: '1.2.0', latest: '', behind: 0, url: 'https://example.invalid/releases',
  checked_at: '', failed: false, enabled: true, interval_hours: 24, commands: [], ...over,
});

const changelog = (update = updateState()) => ({
  current: '1.2.0', source: true, update,
  releases: [
    { version: '1.3.0', date: '2026-09-01', url: '', state: 'ahead',
      sections: [{ title: '⚠ BREAKING CHANGES', entries: ['lanterns need grade B oil'] }] },
    { version: '1.2.0', date: '2026-08-01', url: '', state: 'installed',
      sections: [{ title: 'Features', entries: ['wicks trim themselves'] }] },
  ],
});

const flush = () => new Promise(resolve => setTimeout(resolve, 0));

async function open(update = updateState()) {
  let answer = changelog(update);
  serveApi((path: string) => {
    if (path === '/api/changelog') return answer;
    if (path === '/api/update/check') {
      answer = changelog(updateState({ latest: '1.3.0', behind: 1, commands: ['memai update'] }));
      return answer.update;
    }
    throw new Error(`unexpected ${path}`);
  });
  const view = document.getElementById('view') as HTMLElement;
  await mountView(ChangelogView, view, new URLSearchParams(), { stale: () => false });
  return view;
}

describe('the releases view', () => {
  it('draws the installed version and tags each release by where it stands', async () => {
    const view = await open();
    expect(view.querySelector('.rl-versions.is-current .rl-v-num')?.textContent).toBe('1.2.0');
    const tags = [...view.querySelectorAll('.rl-item .rl-tag')].map(el => el.textContent);
    expect(tags).toEqual([en['rls.state.ahead'], en['rls.state.installed']]);
    expect(view.querySelector('.rl-item.is-here .rl-ver')?.textContent).toBe('1.2.0');
    teardownView();
  });

  it('gives a breaking group its own callout, without the faint glyph', async () => {
    const view = await open();
    const group = view.querySelector('.rl-group.is-breaking .rl-group-title');
    expect(group?.textContent).toBe('⚠️BREAKING CHANGES');
    teardownView();
  });

  it('asks again on Check now and redraws with the newer version and its commands', async () => {
    const view = await open();
    (view.querySelector('#rlCheck') as HTMLButtonElement).click();
    await flush();
    await flush();
    expect(calls.map(c => `${c.method} ${c.path}`)).toContain('POST /api/update/check');
    expect(view.querySelector('.rl-v.is-latest .rl-v-num')?.textContent).toBe('1.3.0');
    expect(view.querySelector('.rl-step.is-cmd code')?.textContent).toBe('memai update');
    expect((view.querySelector('#rlCheck') as HTMLButtonElement).disabled).toBe(false);
    teardownView();
  });

  it('keeps Check now off, and says why, while the release check is switched off', async () => {
    const view = await open(updateState({ enabled: false }));
    const button = view.querySelector('#rlCheck') as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.title).toBe(en['rls.checkOff']);
    teardownView();
  });
});
