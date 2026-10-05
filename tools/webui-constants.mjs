/* Prints the dashboard vocabularies the server has to agree with, as JSON.
   tests/test_webui_vocab.py compares them with memai's own constants. */

import { TYPE_ORDER, REL_SUGGEST, DG_REL_SUGGEST } from '../src/memai/webui/core/vocab.js';
import { DIFF_KINDS, SET_KINDS, FLAG_KINDS, LINE_KINDS, TEXT_KINDS, CONTENT_KINDS,
         WHAT_MIXED } from '../src/memai/webui/core/suggestion-kinds.js';
import { ICON_NAMES } from '../src/memai/webui/core/icons.js';

const list = set => [...set];

console.log(JSON.stringify({
  types: TYPE_ORDER,
  relSuggest: REL_SUGGEST,
  diagramRelSuggest: DG_REL_SUGGEST,
  kinds: {
    diff: list(DIFF_KINDS), set: list(SET_KINDS), flag: list(FLAG_KINDS),
    line: list(LINE_KINDS), text: list(TEXT_KINDS), content: list(CONTENT_KINDS),
  },
  whatMixed: Object.keys(WHAT_MIXED),
  icons: ICON_NAMES,
}));
