/* The vocabularies the dashboard shares with the server, in a module that
   imports nothing, so a script can read them without a browser. */

/* the memory types, in the order every list and legend shows them */
export const TYPE_ORDER = ['note', 'checkpoint', 'anti_pattern', 'reasoning', 'handoff', 'diagram', 'task'];

/* The relation types the pickers offer: between memories, and from a diagram
   step to a memory. The API accepts any string; these are suggestions. */
export const REL_SUGGEST = ['relates_to', 'supersedes', 'contradicts', 'duplicates', 'links_to'];
export const DG_REL_SUGGEST = ['explains', 'contradicts', 'relates_to'];
