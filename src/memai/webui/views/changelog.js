/* Releases: every version this installation can name, and whether it is
   running the newest one.

   Two sources arrive as one list from /api/changelog -- the CHANGELOG.md that
   ships with the package, and, for versions published after it, the release
   check's cache, which a hook process fills (memai/update.py). Nothing here
   reaches the network, so the page reads the same offline; what it cannot
   know is dated by when the check last ran rather than left unsaid.

   It is also the only place in the dashboard that prints a command, and it
   runs none: an install rewrites the environment the MCP servers around it
   are running from, and on Windows it cannot even replace the files while
   they do. The commands are here to be copied and run with everything
   closed. */

import { esc, fmtAgo, fmtDay } from '../core/dom.js';
import { api } from '../core/api.js';
import { copyCode } from '../core/ui.js';
import { icon } from '../core/icons.js';
import { t } from '../i18n.js';

/* Where a version sits relative to the running one. `past` wears nothing:
   the whole list below the current version is past, and a tag on every row
   would mark the rule rather than the exception. */
const TAG = {
  installed: () => `<span class="rl-tag is-here">${t('rls.state.installed')}</span>`,
  ahead: () => `<span class="rl-tag is-ahead">${t('rls.state.ahead')}</span>`,
  past: () => '',
};

export async function renderChangelog(view, params, ctx) {
  const data = await api('/api/changelog');
  if (ctx.stale()) return;
  const state = data.update;

  view.innerHTML = `<div class="anim rl">
    <header class="rl-head">
      <h2 class="rl-title">${t('rls.title')}</h2>
      ${versionsHTML(state)}
    </header>
    ${state.behind ? updateHTML(state) : ''}
    ${data.releases.length
      ? `<div class="rl-stream">${data.releases.map(releaseHTML).join('')}</div>`
      : emptyHTML(state)}
  </div>`;

  for (const button of view.querySelectorAll('[data-copy]')) {
    button.addEventListener('click', () => copyCode(button.dataset.copy));
  }
}

/* ─── where this installation stands ──────────────────────────────────────
   The page's first answer, and the one it is opened for: the version running
   and, when there is one, the version it is behind -- as the two numbers,
   side by side, rather than as a sentence about them. Under the pair, the
   only prose that earns its place here: how far behind, and how fresh the
   answer is. A check that is off or has never run says so, because "no newer
   release" and "nobody looked" are not the same state. */

const bare = version => String(version || '').replace(/^v/, '');

function versionsHTML(state) {
  const installed = `<div class="rl-v">
      <span class="rl-v-num">${esc(bare(state.current))}</span>
      <span class="rl-v-label">${t('rls.installed')}</span>
    </div>`;
  if (!state.behind) {
    return `<div class="rl-versions is-current">
      ${installed}
      <p class="rl-checked">${t('rls.upToDate')} · ${checkedText(state)}</p>
    </div>`;
  }
  return `<div class="rl-versions">
    ${installed}
    <span class="rl-v-step">${icon('chevron-right')}</span>
    <div class="rl-v is-latest">
      <span class="rl-v-num">${esc(bare(state.latest))}</span>
      <span class="rl-v-label">${t('rls.latest')}</span>
    </div>
    <p class="rl-checked">${t('rls.since', { n: state.behind })} · ${checkedText(state)}</p>
  </div>`;
}

function checkedText(state) {
  if (!state.enabled) return t('rls.checkOff');
  if (!state.checked_at) return t('rls.checkNever');
  return t('rls.checked', { ago: fmtAgo(state.checked_at) });
}

/* ─── the update ──────────────────────────────────────────────────────────
   On --raised, which in this stylesheet is the tone of a card that wants a
   decision from the reader. Closing the sessions, the commands and opening
   the host again are steps of one order, so they are one numbered list and
   not a sentence, a list and another sentence. The release page is a control
   under the steps: left in the prose it reads as part of it, and it is what a
   reader reaches for after the instruction rather than before it. */

function updateHTML(state) {
  const steps = state.commands.length
    ? [textStep(t('rls.stepClose')),
       ...state.commands.map(commandStep),
       textStep(t('rls.stepReopen'))]
    : [];
  return `<section class="rl-update">
    <h3 class="rl-update-title">${t('rls.updateTitle', { v: esc(bare(state.latest)) })}</h3>
    <p class="rl-update-lead">${steps.length ? t('rls.updateLead') : t('rls.noCommands')}</p>
    ${steps.length ? `<ol class="rl-steps">${steps.join('')}</ol>` : ''}
    <div class="rl-update-foot">
      <a class="btn btn-sm" href="${esc(state.url)}" target="_blank"
         rel="noopener noreferrer">${t('rls.page')}</a>
    </div>
  </section>`;
}

const textStep = text => `<li class="rl-step"><p class="rl-step-text">${text}</p></li>`;

const commandStep = command => `<li class="rl-step is-cmd">
  <code>${esc(command)}</code>
  <button type="button" class="icon-btn" data-copy="${esc(command)}"
          title="${t('rls.copy')}" aria-label="${t('rls.copy')}">
    ${icon('copy')}</button>
</li>`;

/* ─── one release ─────────────────────────────────────────────────────────
   A document, not a card grid: the version, its date and its standing hold
   the gutter -- sticky, so a long release keeps saying which version is
   being read -- and the entries run beside them at reading measure. */

const releaseHTML = release => `<article class="rl-item${release.state === 'installed' ? ' is-here' : ''}">
  <div class="rl-mark">
    <span class="rl-ver">${esc(release.version)}</span>
    ${release.date
      ? `<time class="rl-date" datetime="${esc(release.date)}">${fmtDay(release.date)}</time>`
      : ''}
    ${(TAG[release.state] || TAG.past)()}
  </div>
  <div class="rl-body">
    ${release.sections.length
      ? release.sections.map(groupHTML).join('')
      : `<p class="rl-quiet">${t('rls.noEntries')}</p>`}
    ${release.url
      ? `<a class="rl-src" href="${esc(release.url)}" target="_blank"
            rel="noopener noreferrer">${t('rls.source')}</a>`
      : ''}
  </div>
</article>`;

const groupHTML = group => `<section class="rl-group">
  ${group.title ? `<h3 class="rl-group-title">${esc(group.title)}</h3>` : ''}
  <ul class="rl-list">${group.entries.map(entry => `<li>${esc(entry)}</li>`).join('')}</ul>
</section>`;

/* ─── nothing to show ─────────────────────────────────────────────────────
   Reached by an installation built outside the release process, which
   carries no history file. It is not an error and not an empty store, so it
   says where the releases are instead of shrugging. */

const emptyHTML = state => `<div class="empty rl-empty">
  <p>${t('rls.noHistory')}</p>
  <p><a href="${esc(state.url)}" target="_blank" rel="noopener noreferrer"
        class="rl-link">${t('rls.page')}</a></p>
</div>`;
