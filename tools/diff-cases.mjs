/* Runs core/textdiff.js over a set of before/after pairs and prints the
   ranges it found, as JSON, so tests/test_textdiff.py can hold the diff to
   them. Usage: node tools/diff-cases.mjs

   Each case prints the two texts with their ranges spliced back in as
   [-...-] and [+...+], which is what the tests read: a range list is a
   pair of numbers and says nothing about which words it covers. */

import { diffRanges } from '../src/memai/webui/core/textdiff.js';

const splice = (text, ranges, open, close) => {
  let out = '', at = 0;
  for (const [s, e] of ranges) {
    out += text.slice(at, s) + open + text.slice(s, e) + close;
    at = e;
  }
  return out + text.slice(at);
};

const CASES = {
  identical: ['the cache warms on boot', 'the cache warms on boot'],
  a_clause_dropped: [
    'A batch retry reruns the whole file upload, not the rows that failed. '
    + 'The second run uploads every row again.',
    'A batch retry reruns the whole file upload, not the rows that failed.',
  ],
  a_clause_added: [
    'The queue drains nightly.',
    'The queue drains nightly, and the batch retries what it could not.',
  ],
  one_word_swapped: [
    'the index rebuild runs on every boot',
    'the index rebuild runs on every deploy',
  ],
  punctuation_survives_its_word: [
    'cache warmup, queue drain, token refresh',
    'cache warmup, token refresh',
  ],
  whitespace_only: ['token   refresh', 'token refresh'],
  empty_before: ['', 'the report export'],
  empty_after: ['the report export', ''],
  a_tag_appended: ['cache, queue, token', 'cache, queue, token, index'],
  a_field_replaced: ['acme/x100', 'acme/x100/p200'],
  short_survivors_are_bridged: [
    'RESULT: the drain counts every message twice.',
    'RESULT: the drain reports a total the batch never wrote.',
  ],
  a_survivor_wider_than_the_bridge_splits_the_mark: [
    'the cache warms before the index rebuild starts every deployment',
    'the queue drains before the index rebuild finishes every deployment',
  ],
  moved_clause: [
    'first the cache warms, then the queue drains',
    'then the queue drains, first the cache warms',
  ],
  long_bodies_over_the_cap: [
    Array.from({ length: 1700 }, (_, i) => `w${i}`).join(' '),
    Array.from({ length: 1700 }, (_, i) => `x${i}`).join(' '),
  ],
};

const out = {};
for (const [name, [a, b]] of Object.entries(CASES)) {
  const { del, ins } = diffRanges(a, b);
  out[name] = {
    del, ins,
    before: splice(a, del, '[-', '-]'),
    after: splice(b, ins, '[+', '+]'),
  };
}
process.stdout.write(JSON.stringify(out, null, 2));
