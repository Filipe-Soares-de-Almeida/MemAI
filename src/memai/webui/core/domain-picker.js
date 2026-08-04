/* The domain filter: core/pick.js with the tree drawn into its rows.

   A domain is a path, so the choice is a TREE -- and this is the control that
   made a popover worth building at all. An <option> renders as text, which
   leaves a native select two ways to say so and no third. Plain indentation
   says how DEEP a row is and not what it hangs off, which is the whole
   question in a store where forty siblings share a parent. Box-drawing
   characters say it, but they need a monospaced face to line up at all and
   still read as ASCII art wedged into a native control -- that shipped once
   and came straight back out.

   So the rows carry the same CSS rails as the Domains table, off the same
   shape function, and the two cannot come to disagree about the tree. The
   filter field is always on rather than at pick.js's own threshold: sixty
   domains under one parent is the normal case here, and typing is what
   actually finds one -- no amount of drawing would have. */

import { esc } from './dom.js';
import { closePicker, pickerHTML, wirePicker } from './pick.js';
import { DOMAIN_SEP, byDomainPath, domainGuides, domainLeaf, domainRailHTML,
         domainSegments } from './shared.js';
import { t } from '../i18n.js';

export { closePicker as closeDomainPicker };

export const domainPickerHTML = ({ id, value = '', ariaLabel }) => pickerHTML({
  id,
  value,
  label: value || t('common.allDomains'),
  ariaLabel: ariaLabel || t('common.allDomains'),
  title: value || t('common.allDomains'),
});

/* `domains` is the tree as the API hands it over; `onPick` gets a full path
   or '' and is what filters. */
export function wireDomainPicker(root, { id, domains, onPick }) {
  wirePicker(root, {
    id,
    items: query => rows(domains, query),
    onPick,
    search: true,
    minWidth: 280,
  });
}

/* Typing narrows the list, and a match brings its ancestors along: they are
   the branch it hangs off, and a child drawn with no parent above it is a
   rail pointing at nothing. They stay selectable -- an ancestor is a real
   scope, and one that just proved it holds what was searched for. */
function matching(domains, query) {
  const list = domains.slice().sort(byDomainPath);
  const needle = query.trim().toLowerCase();
  if (!needle) return list;
  const keep = new Set();
  for (const d of list) {
    if (!d.domain.toLowerCase().includes(needle)) continue;
    keep.add(d.domain);
    const segs = domainSegments(d.domain);
    for (let i = 1; i < segs.length; i++) keep.add(segs.slice(0, i).join(DOMAIN_SEP));
  }
  return list.filter(d => keep.has(d.domain));
}

/* No twist slot here, unlike the table: nothing in this panel expands, so a
   column reserved for a control that never comes would push every row -- a
   root first of all -- in from the edge of its own list. Which means a column
   of the rail is anchored on the NAME of the level above it rather than on
   that level's twist, and .pick-row.dom-row says so with --dom-line. */
function rows(domains, query) {
  const shown = matching(domains, query);
  const guides = domainGuides(shown);
  return [
    { value: '', label: t('common.allDomains'), cls: 'dom-row',
      html: `<span class="dom-leaf any">${t('common.allDomains')}</span>` },
    ...shown.map((d, i) => ({
      value: d.domain,
      /* the whole path is what the filter matches and what the button shows;
         the row itself writes out the leaf, since the rails say the rest */
      label: d.domain,
      title: d.domain,
      cls: 'dom-row',
      style: `--d:${guides[i].depth - 1}`,
      html: `${domainRailHTML(guides[i])}<span class="dom-leaf${
        d.implicit ? ' implicit' : ''}">${esc(domainLeaf(d.domain))}</span>`,
    })),
  ];
}
