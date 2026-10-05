import { describe, expect, it } from 'vitest';
import { tasksPanel } from '../../src/memai/webui/views/overview.js';

describe('the open-tasks figure', () => {
  it('groups the count the way the locale does, and is absent at zero', () => {
    const panel = document.createElement('div');
    panel.innerHTML = tasksPanel(12345);
    expect(panel.querySelector('.hx-tasks-title').textContent).toContain('12,345');
    expect(tasksPanel(0)).toBe('');
  });
});
