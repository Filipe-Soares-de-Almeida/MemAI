/* Circle packing for the graph arrangements.

   `packSiblings` lays circles tangent to one another by front chain and
   `enclose` finds the smallest circle containing a set; both are the
   d3-hierarchy algorithms (Wang et al., Welzl), which nested circles need
   exactly: a domain has to read as one body, and a greedy spiral with
   relaxation leaves a bag of marbles instead.

   Every function here works in world units and writes x/y onto the objects
   it is handed. */

export const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);

const EPS = 1e-6;
const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
const holds = (a, b) => a.r >= dist(a, b) + b.r - EPS;       /* a contains b */
const holdsNot = (a, b) => a.r < dist(a, b) + b.r - EPS;

/* Put `c` tangent to both `a` and `b`. */
function place(b, a, c) {
  const dx = b.x - a.x, dy = b.y - a.y, d2 = dx * dx + dy * dy;
  if (d2) {
    let a2 = a.r + c.r; a2 *= a2;
    let b2 = b.r + c.r; b2 *= b2;
    if (a2 > b2) {
      const x = (d2 + b2 - a2) / (2 * d2);
      const y = Math.sqrt(Math.max(0, b2 / d2 - x * x));
      c.x = b.x - x * dx - y * dy; c.y = b.y - x * dy + y * dx;
    } else {
      const x = (d2 + a2 - b2) / (2 * d2);
      const y = Math.sqrt(Math.max(0, a2 / d2 - x * x));
      c.x = a.x + x * dx - y * dy; c.y = a.y + x * dy + y * dx;
    }
  } else { c.x = a.x + c.r; c.y = a.y; }
}

const overlaps = (a, b) => {
  const dr = a.r + b.r - EPS, dx = b.x - a.x, dy = b.y - a.y;
  return dr > 0 && dr * dr > dx * dx + dy * dy;
};

const score = node => {
  const a = node.c, b = node.next.c, ab = a.r + b.r;
  const dx = (a.x * b.r + b.x * a.r) / ab, dy = (a.y * b.r + b.y * a.r) / ab;
  return dx * dx + dy * dy;
};

const basis1 = a => ({ x: a.x, y: a.y, r: a.r });

function basis2(a, b) {
  const x21 = b.x - a.x, y21 = b.y - a.y, r21 = b.r - a.r;
  const l = Math.hypot(x21, y21);
  return { x: (a.x + b.x + x21 / l * r21) / 2,
           y: (a.y + b.y + y21 / l * r21) / 2,
           r: (l + a.r + b.r) / 2 };
}

function basis3(a, b, c) {
  const a2 = a.x - b.x, a3 = a.x - c.x, b2 = a.y - b.y, b3 = a.y - c.y,
        c2 = b.r - a.r, c3 = c.r - a.r;
  const d1 = a.x * a.x + a.y * a.y - a.r * a.r;
  const d2 = d1 - b.x * b.x - b.y * b.y + b.r * b.r;
  const d3 = d1 - c.x * c.x - c.y * c.y + c.r * c.r;
  const ab = a3 * b2 - a2 * b3;
  const xa = (b2 * d3 - b3 * d2) / (ab * 2) - a.x;
  const xb = (b3 * c2 - b2 * c3) / ab;
  const ya = (a3 * d2 - a2 * d3) / (ab * 2) - a.y;
  const yb = (a2 * c3 - a3 * c2) / ab;
  const A = xb * xb + yb * yb - 1;
  const B = 2 * (a.r + xa * xb + ya * yb);
  const C = xa * xa + ya * ya - a.r * a.r;
  const r = -(Math.abs(A) > 1e-6 ? (B + Math.sqrt(B * B - 4 * A * C)) / (2 * A) : C / B);
  return { x: a.x + xa + xb * r, y: a.y + ya + yb * r, r };
}

const encloseBasis = B =>
  B.length === 1 ? basis1(B[0]) : B.length === 2 ? basis2(B[0], B[1]) : basis3(B[0], B[1], B[2]);

const holdsAll = (e, B) => B.every(q => holds(e, q));

function extendBasis(B, p) {
  if (holdsAll(p, B)) return [p];
  for (let i = 0; i < B.length; i++)
    if (holdsNot(p, B[i]) && holdsAll(basis2(B[i], p), B)) return [B[i], p];
  for (let i = 0; i < B.length - 1; i++)
    for (let j = i + 1; j < B.length; j++)
      if (holdsNot(basis2(B[i], B[j]), p)
          && holdsNot(basis2(B[i], p), B[j])
          && holdsNot(basis2(B[j], p), B[i])
          && holdsAll(basis3(B[i], B[j], p), B))
        return [B[i], B[j], p];
  return [p];
}

/* The smallest circle containing every circle given, as {x, y, r}. */
export function enclose(circles) {
  let i = 0, e = null, B = [];
  while (i < circles.length) {
    const p = circles[i];
    if (e && holds(e, p)) { i++; continue; }
    B = extendBasis(B, p);
    e = encloseBasis(B);
    i = 0;
  }
  return e || { x: 0, y: 0, r: 0 };
}

/* Lay `circles` (each carrying `r`) tangent to one another, centred on their
   enclosing circle. Writes x/y per circle and returns the enclosing radius. */
export function packSiblings(circles) {
  const n = circles.length;
  if (!n) return 0;
  let a = circles[0];
  a.x = 0; a.y = 0;
  if (n === 1) return a.r;
  let b = circles[1];
  a.x = -b.r; b.x = a.r; b.y = 0;
  if (n === 2) return a.r + b.r;
  place(b, a, circles[2]);

  let A = { c: a }, Bn = { c: b }, C = { c: circles[2] };
  A.next = C.prev = Bn; Bn.next = A.prev = C; C.next = Bn.prev = A;
  let head = A, tail = Bn;

  pack: for (let i = 3; i < n; i++) {
    place(head.c, tail.c, circles[i]);
    C = { c: circles[i] };
    let j = tail.next, k = head.prev, sj = tail.c.r, sk = head.c.r;
    do {
      if (sj <= sk) {
        if (overlaps(j.c, C.c)) {
          tail = j; head.next = tail; tail.prev = head; i--; continue pack;
        }
        sj += j.c.r; j = j.next;
      } else {
        if (overlaps(k.c, C.c)) {
          head = k; head.next = tail; tail.prev = head; i--; continue pack;
        }
        sk += k.c.r; k = k.prev;
      }
    } while (j !== k.next);

    C.prev = head; C.next = tail; head.next = tail.prev = tail = C;
    let best = score(head), walk = head;
    while ((walk = walk.next) !== tail) {
      const s = score(walk);
      if (s < best) { head = walk; best = s; }
    }
    tail = head.next;
  }

  const ring = [tail.c];
  let cur = tail;
  while ((cur = cur.next) !== tail) ring.push(cur.c);
  const e = enclose(ring);
  for (const q of circles) { q.x -= e.x; q.y -= e.y; }
  return e.r;
}
