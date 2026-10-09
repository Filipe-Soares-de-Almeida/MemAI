/* The vocabularies the dashboard shares with the server, importing only the contract so a script
   can read them without a browser. */

import { MEMORY } from '../contract.ts';

/* the memory types, in the order every list and legend shows them */
export const TYPE_ORDER = MEMORY.TYPES;

/* The relation types the pickers offer: between memories, and from a diagram
   step to a memory. The API accepts any string; these are suggestions. */
export const REL_SUGGEST = ['relates_to', 'supersedes', 'contradicts', 'duplicates', 'links_to'];
export const DG_REL_SUGGEST = ['explains', 'contradicts', 'relates_to'];

/* the icon inside a task item's state ring; todo is the bare ring */
export const ITEM_MARK = { doing: 'ongoing', done: 'check', dropped: 'minus' };
