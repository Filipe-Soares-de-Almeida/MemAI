/* A memory's body, drawn as what it is rather than as one block of text.

   The store is written by agents, and what they write is marked up: four in
   ten bodies carry code spans, four in ten carry paragraphs, one in six
   carries a list or a link to another memory. A <pre> shows all of that as
   characters.

   What is drawn here is the subset the store actually uses -- headings,
   paragraphs, lists, GFM tables, code spans, bold, links, and the [[uid]]
   that points at another memory. Nothing else is invented: a line this does
   not recognise keeps its whitespace and comes out as it went in, so a shape
   nobody taught it still reads the way it was typed.

   Rendering is READ-ONLY. The body is the record and the editor stays plain
   text -- converting back on save is where text goes missing.

   Everything is escaped BEFORE any tag is added, and the only tags that
   reach the output are the ones built here. `renderRich` returns an HTML
   string; `resolveLinks` maps a uid to what the server said it points at,
   and a uid it has nothing for is drawn as a dead link rather than as a
   button that fails when pressed. */

import { esc } from './dom.js';

const UID = /^[0-9a-f]{16}$/;

/* A row of a GFM table and the line under it that makes it one. Without the
   second, a run of pipes is just text that happens to line up, so it keeps
   its spacing instead of becoming a grid. */
const isPipeRow = ln => {
  const t = ln.trim();
  return t.startsWith('|') && t.endsWith('|') && t.length > 2;
};
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
      /* a name, not a uid: an older store filed memories by slug and those
         references outlived it. Nothing resolves them, so nothing pretends
         to. */
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
  /* an item is kept open until the run moves past it: a deeper list belongs
     INSIDE the <li> above it, and appending to a closed one would make it a
     sibling of the item it is under */
  const items = [];
  let i = at;
  while (i < lines.length) {
    /* A blank line does not end the run. Items with a gap between them are
       one list -- the gap is how a writer spaces a long list out, and
       closing it there restarted the numbering at every item. The run ends
       where the next thing is not an item of this list. */
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

/* Wire every [[uid]] button inside `root` to whatever opens a record. */
export function wireRichLinks(root, open) {
  root.querySelectorAll('.rt-link').forEach(
    b => b.addEventListener('click', e => { e.stopPropagation(); open(b.dataset.uid); }));
}
