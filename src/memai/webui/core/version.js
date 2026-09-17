/* The version mark in the app bar.

   It says which version of MemAI this dashboard is running, and it is the
   way into the releases. When the release check has seen newer versions it
   takes the update icon; how many there are is in the tooltip, because the
   bar has one thing to say here and it is that an update exists. The check
   is made by a hook process and cached (memai/update.py), so this reads a
   file and never waits on a network.

   A request that fails leaves the bar as it was: the version is context, and
   a broken one is not worth an error where the app's identity sits. */

import { $, esc } from './dom.js';
import { api } from './api.js';
import { icon } from './icons.js';
import { t } from '../i18n.js';

export async function mountVersionChip() {
  const host = $('#verHost');
  if (!host) return;
  try { paint(host, await api('/api/update')); }
  catch { /* see the module docstring */ }
}

function paint(host, state) {
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
