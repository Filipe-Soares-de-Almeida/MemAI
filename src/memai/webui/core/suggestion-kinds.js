/* How the optimization view draws each kind of staged suggestion. Imports
   nothing, so a script can check these sets against the server's kinds. */

/* the kinds drawn as a before/after pair; any other kind shows its payload (optRaw) */
export const DIFF_KINDS = new Set(['compact', 'reword', 'retag', 'retitle', 'redomain',
                                   'crosslist', 'set_confidence', 'review', 'archive',
                                   'unleak']);

/* one pane per shape of change: a tag set compared item by item, a flag drawn
   as its pill, a single value on one line each */
export const SET_KINDS = new Set(['retag', 'crosslist']);
export const FLAG_KINDS = new Set(['set_confidence', 'archive']);
export const LINE_KINDS = new Set(['retitle', 'redomain', 'review']);

/* text losing a piece of itself; the payload's `field` says which one */
export const TEXT_KINDS = new Set(['unleak']);

/* the kinds whose "before" is the memory's own content, so the card drops its preview */
export const CONTENT_KINDS = new Set(['compact', 'reword']);

/* the kinds whose group sentence has a `<kind>Mixed` variant, keyed to the
   fact the server leaves empty when the batch disagrees on it */
export const WHAT_MIXED = { redomain: 'to', set_confidence: 'conf', link: 'rel' };
