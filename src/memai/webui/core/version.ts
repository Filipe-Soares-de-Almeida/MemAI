/* The version mark in the app bar, linking to the releases; it shows the update icon when the
   cached release check (memai/update.py) has seen newer versions. A failed request changes nothing. */

import { $, esc } from './dom.ts';
import { update } from '../api/client.ts';
import { icon } from './icons.js';
import { t } from '../i18n.ts';
import type { UpdateState } from '../api/types.ts';

export async function mountVersionChip(): Promise<void> {
  const host = $('#verHost');
  if (!host) return;
  try { paint(host, await update.state()); }
  catch { /* see the module docstring */ }
}

function paint(host: HTMLElement, state: UpdateState): void {
  const behind = Number(state.behind || 0);
  const label = behind
    ? t('ver.behind', { n: behind, latest: esc(state.latest), v: esc(state.current) })
    : t('ver.title', { v: esc(state.current) });
  host.innerHTML = `<a class="ver-chip${behind ? ' is-behind' : ''}" href="#/changelog"
      title="${label}" aria-label="${label}">
    ${behind ? icon('update') : ''}
    <span class="ver-num">v${esc(state.current)}</span>
  </a>`;
}
