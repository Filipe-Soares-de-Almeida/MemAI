/* Every icon in the UI, in one place.

   There is no icon library and no icon font -- these are hand-drawn paths,
   which is why they read as one set. Adding one:

     1. Pick a name and add an entry below.
     2. Draw on a 16x16 grid (the rail, buttons and inline uses all assume
        it). Anything else needs its own viewBox, like brand-seal.
     3. Write ONLY the shapes -- no <svg> wrapper. The wrapper, the
        viewBox and aria-hidden come from icon()/paintIcons().
     4. Leave stroke and fill alone. The consumer styles them, so the same
        path can be a rail icon (stroke: currentColor, width 1.4, via
        admin.css) or a button glyph. Set them inline ONLY to override
        that -- see the filled dot in `memories`, or brand-seal, which is
        not under a .nav a rule and so carries its own.

   Two ways to use one:

     · markup, for a template string   ->  icon('graph', { cls: 'x' })
     · in place, for the static shell  ->  <svg data-icon="graph"></svg>

   The second is why the HTML ships an EMPTY <svg> rather than a
   placeholder div: admin.css sizes `svg` already, so the box is the right
   size from first paint and filling it in cannot shift the layout. Same
   trick, same reason, as data-i18n swapping text in place. */

const ICONS = {
  /* the diamond seal, also drawn by hand into the favicon in index.html --
     the one deliberate copy, because a favicon has to be in the markup
     before any module has loaded */
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

  /* An ACTION: dismiss this. Bare on purpose -- the three confidence
     marks below are ringed, so a cross you can click never reads as a
     cross that is telling you something. That collision is why this set
     exists: both used to be the same character, U+2715. */
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
  /* the spelling-drift marker in the domain table: two tildes, ≈ */
  approx: {
    viewBox: '0 0 16 16',
    body: `<path d="M2.4 6.1c1-1.5 2-1.5 3 0s2 1.5 3 0 2-1.5 3 0"/>
           <path d="M2.4 10.4c1-1.5 2-1.5 3 0s2 1.5 3 0 2-1.5 3 0"/>`,
  },

  /* ── confidence marks ──────────────────────────────────────────────
     All three are RINGED, which is the identity: a ring says "this is a
     state of the record", never "press me". The ring is also what keeps
     them apart from `close` and from each other at 12px, where the inner
     shape alone is nearly nothing. */

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

  /* ── direction ──────────────────────────────────────────────────────
     One drawing, rotated, so an inbound and an outbound relation cannot
     drift into different arrowheads. */

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
  /* paging, where a full arrow reads as heavier than the step it takes */
  'chevron-left': {
    viewBox: '0 0 16 16',
    body: `<path d="M10 3.2L5.2 8l4.8 4.8"/>`,
  },
  'chevron-right': {
    viewBox: '0 0 16 16',
    body: `<path d="M6 3.2L10.8 8 6 12.8"/>`,
  },
};

export const iconNames = () => Object.keys(ICONS);

/* Markup for a template string.

   Every icon carries `.ico`, which is where admin.css says how these are
   drawn (stroke, caps, and a --ico size custom property). A caller adds
   its own class for anything beyond that; it does not restate the stroke.

   Decorative by default: an icon in this UI sits next to its own label,
   so announcing it twice is noise. Pass `title` only when the icon is the
   ONLY thing naming its control -- an icon-only button. */
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
