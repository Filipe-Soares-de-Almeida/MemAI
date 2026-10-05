/* Every icon in the UI: hand-drawn shapes on a 16x16 grid unless an entry has its own viewBox,
   with no <svg> wrapper and no stroke or fill unless overriding admin.css. */

const ICONS = {
  /* the diamond seal; the favicon in index.html is a hand copy, needed before any module loads */
  'brand-seal': {
    viewBox: '0 0 34 34',
    body: `<rect x="8" y="8" width="18" height="18" rx="2.5" transform="rotate(45 17 17)"
                 fill="none" stroke="currentColor" stroke-width="1.4"/>
           <rect x="12.5" y="12.5" width="9" height="9" rx="1.5" transform="rotate(45 17 17)"
                 fill="none" stroke="currentColor" stroke-width="1" opacity=".55"/>
           <circle cx="17" cy="17" r="2" fill="currentColor"/>`,
  },

  /* ── rail sections ── */

  overview: {   /* a 2x2 grid of tiles */
    viewBox: '0 0 16 16',
    body: `<rect x="1.5" y="1.5" width="5.4" height="5.4" rx="1"/>
           <rect x="9.1" y="1.5" width="5.4" height="5.4" rx="1"/>
           <rect x="1.5" y="9.1" width="5.4" height="5.4" rx="1"/>
           <rect x="9.1" y="9.1" width="5.4" height="5.4" rx="1"/>`,
  },
  memories: {   /* list rows, the last one bulleted */
    viewBox: '0 0 16 16',
    body: `<path d="M2 3.5h12M2 8h12M2 12.5h8"/>
           <circle cx="13.4" cy="12.5" r="1.1" fill="currentColor" stroke="none"/>`,
  },
  graph: {      /* three connected nodes */
    viewBox: '0 0 16 16',
    body: `<circle cx="3.4" cy="12.4" r="1.9"/><circle cx="12.5" cy="11" r="1.9"/>
           <circle cx="7.6" cy="3.4" r="1.9"/>
           <path d="M6.7 5.1 4.2 10.6M9 4.7l2.6 4.6M5.3 12.2l5.3-.9"/>`,
  },
  diagrams: {   /* one box branching into two */
    viewBox: '0 0 16 16',
    body: `<rect x="5.1" y="1.3" width="5.8" height="3.4" rx=".8"/>
           <rect x="1.4" y="11.3" width="5.4" height="3.4" rx=".8"/>
           <rect x="9.2" y="11.3" width="5.4" height="3.4" rx=".8"/>
           <path d="M8 4.7v2.6M8 7.3H4.1v4M8 7.3h3.9v4"/>`,
  },
  domains: {    /* a folder */
    viewBox: '0 0 16 16',
    body: `<path d="M1.5 4.2c0-.7.5-1.2 1.2-1.2h3l1.5 1.7h6.1c.7 0 1.2.5 1.2 1.2v6.4c0 .7-.5 1.2-1.2 1.2H2.7c-.7 0-1.2-.5-1.2-1.2V4.2z"/>`,
  },
  maintenance: {  /* two sliders */
    viewBox: '0 0 16 16',
    body: `<path d="M2 4.5h8.2M13.8 4.5H14M2 11.5h1.8M7.4 11.5H14"/>
           <circle cx="11.9" cy="4.5" r="1.7"/><circle cx="5.6" cy="11.5" r="1.7"/>`,
  },
  optimization: {  /* rays around a core */
    viewBox: '0 0 16 16',
    body: `<path d="M8 1.5v2.2M8 12.3v2.2M1.5 8h2.2M12.3 8h2.2M3.4 3.4l1.6 1.6M11 11l1.6 1.6M12.6 3.4 11 5M5 11l-1.6 1.6"/>
           <circle cx="8" cy="8" r="2.4"/>`,
  },

  /* ── chrome ── */

  search: {
    viewBox: '0 0 16 16',
    body: `<circle cx="7" cy="7" r="4.6"/><path d="m10.6 10.6 3 3"/>`,
  },

  /* An action, dismiss: bare, so it never reads as one of the ringed confidence marks. */
  close: {
    viewBox: '0 0 16 16',
    body: `<path d="M4 4l8 8M12 4l-8 8"/>`,
  },
  pencil: {
    viewBox: '0 0 16 16',
    body: `<path d="M11.1 2.4a1.3 1.3 0 0 1 1.9 0l.6.6a1.3 1.3 0 0 1 0 1.9l-7 7-3 .9.9-3z"/>
           <path d="M10.2 3.3l2.5 2.5"/>`,
  },
  info: {
    viewBox: '0 0 16 16',
    body: `<circle cx="8" cy="8" r="6.2"/><path d="M8 7.2v4"/>
           <circle cx="8" cy="4.9" r=".85" fill="currentColor" stroke="none"/>`,
  },
  /* Destroying what a row names, distinct from dismissing the row (`close`). */
  trash: {
    viewBox: '0 0 16 16',
    body: `<path d="M2.6 4.4h10.8"/>
           <path d="M6.2 4.4V2.8h3.6v1.6"/>
           <path d="M4 4.4l.6 8.4a1.2 1.2 0 0 0 1.2 1.1h4.4a1.2 1.2 0 0 0 1.2-1.1l.6-8.4"/>
           <path d="M6.8 7v4.2M9.2 7v4.2"/>`,
  },
  /* the spelling-drift marker in the domain table: two tildes, ≈ */
  approx: {
    viewBox: '0 0 16 16',
    body: `<path d="M2.4 6.1c1-1.5 2-1.5 3 0s2 1.5 3 0 2-1.5 3 0"/>
           <path d="M2.4 10.4c1-1.5 2-1.5 3 0s2 1.5 3 0 2-1.5 3 0"/>`,
  },

  /* ── confidence marks: all RINGED, meaning a record state, not a control; the ring also keeps
     them distinct at 12px. */

  confirmed: {
    viewBox: '0 0 16 16',
    body: `<circle cx="8" cy="8" r="6.2"/><path d="M5.2 8.2l2 2 3.6-4.2"/>`,
  },
  unverified: {   /* an open ring: nothing has been decided yet */
    viewBox: '0 0 16 16',
    body: `<circle cx="8" cy="8" r="6.2" stroke-dasharray="2.4 2.1"/>`,
  },
  contradicted: {
    viewBox: '0 0 16 16',
    body: `<circle cx="8" cy="8" r="6.2"/><path d="M5.8 5.8l4.4 4.4M10.2 5.8l-4.4 4.4"/>`,
  },

  /* ── direction: one drawing, rotated, so inbound and outbound arrowheads match. */

  'arrow-right': {
    viewBox: '0 0 16 16',
    body: `<path d="M2.5 8h11M9.4 4.1L13.5 8l-4.1 3.9"/>`,
  },
  'arrow-left': {
    viewBox: '0 0 16 16',
    body: `<path d="M13.5 8h-11M6.6 4.1L2.5 8l4.1 3.9"/>`,
  },
  'arrow-down': {
    viewBox: '0 0 16 16',
    body: `<path d="M8 2.5v11M4.1 9.4L8 13.5l3.9-4.1"/>`,
  },
  /* ── line samples for the canvas legend (.ico-line): each repeats what drawEdge draws, so
     the two change together. */

  'line-plain': {
    viewBox: '0 0 22 7',
    body: `<path d="M1 3.5h20"/>`,
  },
  'line-loop': {   /* dashed: closes a cycle -- see drawEdge's `back` */
    viewBox: '0 0 22 7',
    body: `<path d="M1 3.5h20" stroke-dasharray="4.5 3.5"/>`,
  },
  'line-hot': {    /* thicker: the selected connection */
    viewBox: '0 0 22 7',
    body: `<path d="M1 3.5h20" stroke-width="2.6"/>`,
  },

  /* paging, where a full arrow reads as heavier than the step it takes */
  /* two sheets, the front one offset -- the shape everything uses for copy */
  copy: {
    viewBox: '0 0 16 16',
    body: `<rect x="5.5" y="5.5" width="8" height="8" rx="1.5" fill="none"
                 stroke="currentColor" stroke-width="1.3"/>
           <path d="M10.5 3.2A1.7 1.7 0 0 0 8.8 2H4.2A2.2 2.2 0 0 0 2 4.2v4.6A1.7 1.7 0 0 0 3.2 10.5"
                 fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>`,
  },
  check: {
    viewBox: '0 0 16 16',
    body: `<path d="M3 8.4l3.2 3.2L13 4.8" pathLength="1" fill="none" stroke="currentColor"
                 stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>`,
  },
  ongoing: {  /* one open arc ending in an arrowhead: a task item in progress */
    viewBox: '0 0 16 16',
    body: `<path d="M10.29 14.3A6.7 6.7 0 1 1 13.8 4.65" fill="none" stroke="currentColor"
                 stroke-width="1.6" stroke-linecap="round"/>
           <path d="M11.71 5.63L15 6.73l.69-3.4z" fill="currentColor" stroke="currentColor"
                 stroke-width=".6" stroke-linejoin="round"/>`,
  },
  minus: {    /* set aside: a task item that was dropped */
    viewBox: '0 0 16 16',
    body: `<path d="M4 8h8"/>`,
  },
  comment: {  /* a speech bubble with its tail at the lower left */
    viewBox: '0 0 16 16',
    body: `<path d="M3.2 2.8h9.6a1 1 0 011 1v6.4a1 1 0 01-1 1H7.4L4.4 13.6v-2.4H3.2a1 1 0 01-1-1V3.8a1 1 0 011-1z"/>`,
  },
  person: {   /* a head over shoulders: a comment written by a person */
    viewBox: '0 0 16 16',
    body: `<circle cx="8" cy="5.6" r="2.4"/>
           <path d="M3.2 13.2c.4-2.4 2.2-3.7 4.8-3.7s4.4 1.3 4.8 3.7"/>`,
  },
  agent: {    /* a prompt: a comment written by an agent's session */
    viewBox: '0 0 16 16',
    body: `<path d="M3.6 5.4L6.9 8l-3.3 2.6"/>
           <path d="M8.6 11h3.8"/>`,
  },
  more: {     /* three dots in a row: the menu behind a control */
    viewBox: '0 0 16 16',
    body: `<path d="M3.5 8h.01M8 8h.01M12.5 8h.01" stroke-width="2.4"/>`,
  },
  update: {   /* something to bring down onto this machine */
    viewBox: '0 0 16 16',
    body: `<path d="M8 2.4v6.6"/>
           <path d="M5 6.3l3 3 3-3"/>
           <path d="M3 12.8h10"/>`,
  },
  'chevron-left': {
    viewBox: '0 0 16 16',
    body: `<path d="M10 3.2L5.2 8l4.8 4.8"/>`,
  },
  'chevron-right': {
    viewBox: '0 0 16 16',
    body: `<path d="M6 3.2L10.8 8 6 12.8"/>`,
  },

  /* the handle a domain level is dragged by: two columns of dots, the
     convention for "this can be picked up and moved" */
  'grip': {
    viewBox: '0 0 16 16',
    body: `<path d="M5.5 4h.01M10.5 4h.01M5.5 8h.01M10.5 8h.01M5.5 12h.01M10.5 12h.01"/>`,
  },

  /* a shelf of backups: the folder that holds one project's files */
  'folder': {
    viewBox: '0 0 16 16',
    body: `<path d="M2 4.2a1 1 0 011-1h3.2l1.3 1.5H13a1 1 0 011 1v6.1a1 1 0 01-1 1H3a1 1 0 01-1-1z"/>`,
  },
  /* a pushpin, filled when pinned and an outline when not: a backup held back
     from a sweep, or a memory pinned for every domain */
  'pin': {
    viewBox: '0 0 16 16',
    body: `<path d="M6.3 2.6h3.4v3.5l1.9 3H4.4l1.9-3z"/>
           <path d="M5.2 2.6h5.6M8 9.1v4.5"/>`,
  },
  /* a memory pinned to its own domain: the same pushpin, stuck into a base
     line -- the one place it holds to */
  'pin-domain': {
    viewBox: '0 0 16 16',
    body: `<path d="M6.3 1.8h3.4v3.3l1.9 2.9H4.4l1.9-2.9z"/>
           <path d="M5.2 1.8h5.6M8 8v4.4M4.2 12.6h7.6"/>`,
  },
  /* the filters a list keeps folded away: a funnel */
  'filter': {
    viewBox: '0 0 16 16',
    body: `<path d="M2.6 3.4h10.8L9.2 8.4v3.9l-2.4 1.2V8.4z"/>`,
  },
  /* a zip of backups: the same box the folder is, closed down the middle */
  'archive': {
    viewBox: '0 0 16 16',
    body: `<rect x="2.5" y="3.2" width="11" height="9.6" rx="1"/>
           <path d="M8 3.4v9.2" stroke-dasharray="1.6 1.4"/>`,
  },
  /* one backup: a sheet with rows, the last one short */
  'db-file': {
    viewBox: '0 0 16 16',
    body: `<rect x="2.5" y="2.5" width="11" height="11" rx="1.4"/>
           <path d="M5.2 6.4h5.6M5.2 8.6h5.6M5.2 10.8h3.4"/>`,
  },

  /* the relations graph's show toggles: a relation and a name */
  'relation': {
    viewBox: '0 0 16 16',
    body: `<circle cx="3.6" cy="8" r="2"/><circle cx="12.4" cy="8" r="2"/>
           <path d="M5.7 8h4.6"/>`,
  },
  'label': {
    viewBox: '0 0 16 16',
    body: `<path d="M2.6 7.6V3.3a.8.8 0 01.8-.8h4.3l5.8 5.8a.8.8 0 010 1.1l-3.5 3.5a.8.8 0 01-1.1 0z"/>
           <circle cx="5.5" cy="5.5" r="1"/>`,
  },
};

/* every name icon() and data-icon accept */
export const ICON_NAMES = Object.keys(ICONS);



/* Markup for a template string; `.ico` gets its stroke and size from admin.css. Decorative by
   default: pass `title` only for an icon-only control. */
export function icon(name, { cls = '', title = '' } = {}) {
  const it = ICONS[name];
  if (!it) {
    console.error(`icon: no such icon '${name}'`);
    return '';
  }
  const attrs = [
    `class="ico${cls ? ` ${cls}` : ''}"`,
    `viewBox="${it.viewBox}"`,
    title ? `role="img" aria-label="${title}"` : 'aria-hidden="true"',
  ].join(' ');
  return `<svg ${attrs}>${it.body}</svg>`;
}

/* Fill in every <svg data-icon="..."> under `root`, in place. Idempotent,
   so it is safe on a subtree that has already been painted. */
export function paintIcons(root = document) {
  for (const el of root.querySelectorAll('[data-icon]')) {
    const it = ICONS[el.dataset.icon];
    if (!it) {
      console.error(`icon: no such icon '${el.dataset.icon}'`);
      continue;
    }
    el.setAttribute('viewBox', it.viewBox);
    if (!el.hasAttribute('aria-label')) el.setAttribute('aria-hidden', 'true');
    el.innerHTML = it.body;
  }
}
