/* A word-level diff between two bodies and its marks. Offsets index RENDERED text, so markPair()
   takes both diff and marks from the same two elements. */

/* A word, a whitespace run, or one punctuation mark, so a surviving comma is not marked. */
const TOKEN = /[\p{L}\p{N}_]+|\s+|[^\p{L}\p{N}_\s]/gu;

/* Tokens per side, after the shared head and tail, that get an LCS table ((n+1)(m+1) Uint16,
   4.5MB at most); past it the changed middle is marked whole. */
const CAP = 1500;

const tokenize = text => text.match(TOKEN) || [];

const WS = /^\s*$/;

/* Unchanged characters between two changes that get absorbed into one mark, so a rewritten
   clause reads as one change and not as fragments around "the" and "and". */
const BRIDGE = 12;

/* Ranges trimmed of edge whitespace, all-whitespace ones dropped, near neighbours merged. */
function tidy(text, ranges) {
  const out = [];
  for (const r of ranges) {
    let [s, e] = r;
    while (s < e && WS.test(text[s])) s++;
    while (e > s && WS.test(text[e - 1])) e--;
    if (e <= s) continue;
    const last = out[out.length - 1];
    if (last && s - last[1] <= BRIDGE && !text.slice(last[1], s).includes('\n')) last[1] = e;
    else out.push([s, e]);
  }
  return out;
}

/* What one text drops and the other adds: {del, ins} as sorted, non-overlapping ranges into
   `before` and `after`; identical texts give two empty lists. */
export function diffRanges(before, after) {
  const a = String(before ?? ''), b = String(after ?? '');
  if (a === b) return { del: [], ins: [] };
  const A = tokenize(a), B = tokenize(b);

  let head = 0;
  while (head < A.length && head < B.length && A[head] === B[head]) head++;
  let tail = 0;
  while (tail < A.length - head && tail < B.length - head
         && A[A.length - 1 - tail] === B[B.length - 1 - tail]) tail++;

  const midA = A.slice(head, A.length - tail);
  const midB = B.slice(head, B.length - tail);
  const offA = A.slice(0, head).join('').length;
  const offB = B.slice(0, head).join('').length;
  const n = midA.length, m = midB.length;
  if (!n && !m) return { del: [], ins: [] };
  if (n > CAP || m > CAP) {
    return {
      del: tidy(a, [[offA, offA + midA.join('').length]]),
      ins: tidy(b, [[offB, offB + midB.join('').length]]),
    };
  }

  const w = m + 1;
  const lcs = new Uint16Array((n + 1) * w);
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i * w + j] = midA[i] === midB[j]
        ? lcs[(i + 1) * w + j + 1] + 1
        : Math.max(lcs[(i + 1) * w + j], lcs[i * w + j + 1]);
    }
  }

  const del = [], ins = [];
  let i = 0, j = 0, pa = offA, pb = offB, dOpen = -1, iOpen = -1;
  const closeDel = () => { if (dOpen >= 0) { del.push([dOpen, pa]); dOpen = -1; } };
  const closeIns = () => { if (iOpen >= 0) { ins.push([iOpen, pb]); iOpen = -1; } };
  while (i < n || j < m) {
    if (i < n && j < m && midA[i] === midB[j]) {
      closeDel(); closeIns();
      pa += midA[i++].length;
      pb += midB[j++].length;
    } else if (j >= m || (i < n && lcs[(i + 1) * w + j] >= lcs[i * w + j + 1])) {
      if (dOpen < 0) dOpen = pa;
      pa += midA[i++].length;
    } else {
      if (iOpen < 0) iOpen = pb;
      pb += midB[j++].length;
    }
  }
  closeDel(); closeIns();
  return { del: tidy(a, del), ins: tidy(b, ins) };
}

const textNodes = el => {
  const walk = el.ownerDocument.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  const out = [];
  for (let n = walk.nextNode(); n; n = walk.nextNode()) out.push(n);
  return out;
};

/* Wraps each range in a <mark>, cutting back to front so earlier offsets stay valid. */
function paint(nodes, ranges, cls) {
  if (!ranges.length) return;
  const cuts = [];
  let at = 0;
  for (const node of nodes) {
    const end = at + node.nodeValue.length;
    for (const [s, e] of ranges) {
      if (s >= end) break;
      const from = Math.max(s, at) - at, to = Math.min(e, end) - at;
      if (to > from) cuts.push([node, from, to]);
    }
    at = end;
  }
  for (let k = cuts.length - 1; k >= 0; k--) {
    const [node, from, to] = cuts[k];
    const part = from ? node.splitText(from) : node;
    if (to - from < part.nodeValue.length) part.splitText(to - from);
    const mark = part.ownerDocument.createElement('mark');
    mark.className = cls;
    part.parentNode.replaceChild(mark, part);
    mark.appendChild(part);
  }
}

/* Marks changes in place in a before/after pair, whose final markup must already be in place;
   marking twice would diff the marks. */
export function markPair(beforeEl, afterEl) {
  if (!beforeEl || !afterEl) return;
  const a = textNodes(beforeEl), b = textNodes(afterEl);
  const join = nodes => nodes.map(n => n.nodeValue).join('');
  const { del, ins } = diffRanges(join(a), join(b));
  paint(a, del, 'df-del');
  paint(b, ins, 'df-ins');
}
