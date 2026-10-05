import { describe, expect, it } from 'vitest';
import { renderMaintenance } from '../../src/memai/webui/views/maintenance.js';
import { catalog, serveApi } from './support.js';

const en = catalog('en');

describe('the interface tab', () => {
  it('sets the motion mode, which the root and the next visit both read', async () => {
    serveApi(() => { throw new Error('offline'); });
    const view = document.getElementById('view');
    await renderMaintenance(view, new URLSearchParams({ tab: 'interface' }));
    const tab = view.querySelector('#mntTab-interface');
    expect(tab.textContent.trim()).toBe(en['mn.tab.interface']);
    expect(tab.getAttribute('aria-selected')).toBe('true');

    view.querySelector('#uiMotion').click();
    const offered = [...document.querySelectorAll('.pick-pop [role="option"]')].map(o => o.textContent.trim());
    expect(offered).toEqual(['mn.ui.system', 'mn.ui.always', 'mn.ui.never'].map(k => en[k]));
    document.querySelector('.pick-pop [role="option"][data-v="never"]').click();
    expect(document.documentElement.dataset.motion).toBe('reduce');
    expect(localStorage.getItem('memai.motion')).toBe('never');

    view.querySelector('#uiMotion').click();
    document.querySelector('.pick-pop [role="option"][data-v="always"]').click();
    expect(document.documentElement.dataset.motion).toBe('full');
    localStorage.removeItem('memai.motion');
  });
});
