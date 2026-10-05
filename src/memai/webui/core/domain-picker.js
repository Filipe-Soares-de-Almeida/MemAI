/* The domain filter: core/pick.js with the Domains table's CSS rails drawn from the same shape
   function, and the filter field always on. */

import { esc } from './dom.js';
import { closePicker, pickerHTML, wirePicker } from './pick.js';
import { DOMAIN_SEP, byDomainPath, domainGuides, domainLeaf, domainRailHTML,
         domainSegments } from './shared.js';
import { t } from '../i18n.js';

export { closePicker as closeDomainPicker };

/* `anyLabel` is what the empty value says: "All domains" for a filter, something else where ''
   means otherwise (the bulk re-home field: "leave each memory where it is"). */
export const domainPickerHTML = ({ id, value = '', ariaLabel, anyLabel = '', cls = '' }) => {
  const none = anyLabel || t('common.allDomains');
  return pickerHTML({
    id,
    value,
    cls,
    label: value || none,
    ariaLabel: ariaLabel || none,
    title: value || none,
  });
};

/* `domains` is the tree as the API hands it over; `onPick` gets a full path
   or '' and is what filters. */
export function wireDomainPicker(root, { id, domains, onPick, anyLabel = '' }) {
  wirePicker(root, {
    id,
    items: query => rows(domains, query, anyLabel),
    onPick,
    search: true,
    minWidth: 280,
  });
}

/* Typing narrows the list; a match brings its ancestors, which stay selectable scopes. */
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

/* No twist slot, since nothing here expands: rails anchor on the name of the level above, which
   .pick-row.dom-row declares with --dom-line. */
function rows(domains, query, anyLabel = '') {
  const shown = matching(domains, query);
  const guides = domainGuides(shown);
  const none = anyLabel || t('common.allDomains');
  return [
    { value: '', label: none, cls: 'dom-row',
      html: `<span class="dom-leaf any">${esc(none)}</span>` },
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
