/* A memory body drawn read-only as headings, paragraphs, lists, GFM tables, code, bold and [[uid]]
   links; unknown lines keep their whitespace. Everything is escaped before any tag is added. */

import { esc } from './dom.js';
import { icon } from './icons.js';
import { t } from '../i18n.js';

const UID = /^[0-9a-f]{16}$/;

/* A GFM table row plus the separator line that makes it one; without it, pipes stay plain text. */
const isPipeRow = ln => {
  const t = ln.trim();
  return t.startsWith('|') && t.endsWith('|') && t.length > 2;
};
const FENCE = /^\s*```([A-Za-z0-9_+#-]*)\s*$/;
const SEPARATOR = /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$/;
const HEADING = /^\s*={2,}\s+(.+?)\s+={2,}\s*$/;
const ITEM = /^(\s*)([-*]|\d+[.)])\s+(.*)$/;

const cells = ln => ln.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());

const alignOf = spec => {
  const t = spec.trim();
  if (t.startsWith(':') && t.endsWith(':')) return 'center';
  if (t.endsWith(':')) return 'right';
  return '';
};

/* ── inline ──────────────────────────────────────────────────────────────
   Code spans are taken out first and put back last: what is inside one is
   text, so a `**` or a [[uid]] in there must survive as the characters it
   is. The placeholder is a control character, which cannot appear in the
   escaped text it is spliced into. */

const SLOT = String.fromCharCode(0);

function inline(raw, links) {
  const code = [];
  let text = esc(raw).replace(/`([^`\n]+)`/g, (_, body) => {
    code.push(body);
    return SLOT;
  });

  text = text.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');

  text = text.replace(/\[\[([^\]\n]+)\]\]/g, (whole, target) => {
    if (!UID.test(target)) {
      /* a name, not a uid: slug references nothing resolves, so nothing pretends to */
      return `<span class="rt-link-plain" title="${esc(whole)}">${target}</span>`;
    }
    const found = links && links[target];
    if (!found || found.missing) {
      return `<span class="rt-link-dead" title="${esc(target)}">${target}</span>`;
    }
    const hint = [found.type, found.domain, found.snippet].filter(Boolean).join(' · ');
    return `<button type="button" class="rt-link${found.linked ? '' : ' rt-link-unlinked'}"`
      + ` data-uid="${esc(target)}" title="${esc(hint)}">${target}</button>`;
  });

  text = text.replace(/(https?:\/\/[^\s<]+)/g, (url) => {
    const trail = url.match(/[.,;:!?)\]]+$/);
    const href = trail ? url.slice(0, -trail[0].length) : url;
    return `<a class="rt-url" href="${href}" target="_blank" rel="noopener noreferrer">${href}</a>`
      + (trail ? trail[0] : '');
  });

  let i = 0;
  return text.replace(new RegExp(SLOT, 'g'), () => `<code>${code[i++]}</code>`);
}

/* ── blocks ───────────────────────────────────────────────────────────── */

function table(rows, links) {
  const head = cells(rows[0]);
  const align = cells(rows[1]).map(alignOf);
  const style = i => (align[i] ? ` style="text-align:${align[i]}"` : '');
  const body = rows.slice(2).map(r => {
    const cs = cells(r);
    /* a short row is padded rather than dropped: the columns after it are
       empty, which is what the writer typed */
    return `<tr>${head.map((_, i) => `<td${style(i)}>${inline(cs[i] || '', links)}</td>`).join('')}</tr>`;
  }).join('');
  return `<div class="rt-table-wrap"><table class="rt-table"><thead><tr>`
    + head.map((h, i) => `<th${style(i)}>${inline(h, links)}</th>`).join('')
    + `</tr></thead><tbody>${body}</tbody></table></div>`;
}

/* One level of items and everything indented under them. `at` is where the
   run starts; the caller gets back the html and where to carry on from. */
function list(lines, at, links) {
  const opener = ITEM.exec(lines[at]);
  const base = opener[1].length;
  const ordered = /^\d/.test(opener[2]);
  /* the writer's own numbering, so a run that opens at 3 is not renumbered
     from 1 -- the browser counts, but only from where it is told to start */
  const from = ordered ? parseInt(opener[2], 10) : 1;
  let loose = false;
  /* an item stays open until the run moves past it, so a deeper list nests INSIDE its <li> */
  const items = [];
  let i = at;
  while (i < lines.length) {
    /* A blank line does not end the run, so spaced-out items stay one list and keep their
       numbering; it ends at the next line that is not an item. */
    if (!lines[i].trim()) {
      let ahead = i;
      while (ahead < lines.length && !lines[ahead].trim()) ahead += 1;
      const next = ahead < lines.length ? ITEM.exec(lines[ahead]) : null;
      if (!next || next[1].length < base) break;
      loose = true;
      i = ahead;
      continue;
    }
    const m = ITEM.exec(lines[i]);
    if (!m || m[1].length < base) break;
    if (m[1].length > base) {
      if (!items.length) break;
      const nested = list(lines, i, links);
      items[items.length - 1] += nested.html;
      i = nested.next;
      continue;
    }
    /* a line under an item that opens no item of its own continues it --
       a wrapped sentence, not a new point */
    const parts = [m[3]];
    i += 1;
    while (i < lines.length && lines[i].trim() && !ITEM.test(lines[i]) && !HEADING.test(lines[i])
           && lines[i].startsWith(' '.repeat(base + 1))) {
      parts.push(lines[i].trim());
      i += 1;
    }
    items.push(inline(parts.join('\n'), links));
  }
  const tag = ordered ? 'ol' : 'ul';
  const attrs = `class="rt-list${loose ? ' rt-list-loose' : ''}"`
    + (ordered && from !== 1 ? ` start="${from}"` : '');
  return {
    html: `<${tag} ${attrs}>${items.map(it => `<li>${it}</li>`).join('')}</${tag}>`,
    next: i,
  };
}

/* The headings a body opens, in renderRich's `<h4 class="rt-h">` order; both read HEADING, so a
   caller can scroll to the nth `.rt-h`. */
export function headings(body) {
  return String(body ?? '').split('\n')
    .map(line => HEADING.exec(line)?.[1].trim())
    .filter(Boolean);
}

export function renderRich(body, links) {
  const lines = String(body ?? '').split('\n');
  const out = [];
  let i = 0;
  let para = [];

  const flush = () => {
    if (!para.length) return;
    out.push(`<p class="rt-p">${inline(para.join('\n'), links)}</p>`);
    para = [];
  };

  while (i < lines.length) {
    const line = lines[i];

    /* A fenced block is verbatim; an unclosed fence runs to the end of the body. */
    const fence = FENCE.exec(line);
    if (fence) {
      flush();
      const body = [];
      i += 1;
      while (i < lines.length && !FENCE.test(lines[i])) { body.push(lines[i]); i += 1; }
      i += 1;   // past the closing fence, or past the end
      const lang = fence[1].toLowerCase();
      /* The language gets its own bar, which also holds the copy button, so it never overlaps
         the code. */
      out.push(`<div class="rt-code-block">`
        + `<div class="rt-code-head">`
        + `<span class="rt-code-lang">${esc(lang)}</span>`
        + `<button type="button" class="rt-code-copy" data-copy-code`
        + ` title="${esc(t('rt.copy'))}" aria-label="${esc(t('rt.copy'))}">`
        + `${icon('copy')}</button></div>`
        + `<pre class="rt-code"><code${lang ? ` data-lang="${esc(lang)}"` : ''}>`
        + `${esc(body.join('\n'))}</code></pre></div>`);
      continue;
    }

    if (!line.trim()) { flush(); i += 1; continue; }

    const heading = HEADING.exec(line);
    if (heading) {
      flush();
      out.push(`<h4 class="rt-h">${inline(heading[1], links)}</h4>`);
      i += 1;
      continue;
    }

    if (isPipeRow(line)) {
      const rows = [];
      while (i < lines.length && isPipeRow(lines[i])) { rows.push(lines[i]); i += 1; }
      flush();
      if (rows.length >= 2 && SEPARATOR.test(rows[1].trim())) out.push(table(rows, links));
      /* pipes with no separator under them are not a grid, and collapsing
         their spacing would lose the alignment the writer lined up by hand */
      else out.push(`<pre class="rt-raw">${inline(rows.join('\n'), links)}</pre>`);
      continue;
    }

    if (ITEM.test(line)) {
      flush();
      const built = list(lines, i, links);
      out.push(built.html);
      i = built.next;
      continue;
    }

    para.push(line);
    i += 1;
  }
  flush();
  return out.join('');
}

/* Wire what was drawn: [[uid]] opens its record, code blocks copy through `copy` (core/ui.js). */
export function wireRich(root, { open, copy }) {
  root.querySelectorAll('.rt-link').forEach(
    b => b.addEventListener('click', e => { e.stopPropagation(); open(b.dataset.uid); }));
  root.querySelectorAll('[data-copy-code]').forEach(b => b.addEventListener('click', e => {
    e.stopPropagation();
    copy(b.closest('.rt-code-block').querySelector('code').textContent);
  }));
}
