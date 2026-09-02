/* The relations universe: memories as stars in three dimensions.

   Two canvases and one arrangement. The stars, their halos and the relation
   filaments are drawn by WebGL2 -- one instanced pass for every memory, one
   line pass for every relation, one point pass for the sky -- so the store's
   size costs the graphics card and not the frame. The names on top are drawn
   by a 2D canvas over it, because a handful of legible labels is a typography
   job and not a shader one.

   Where the nodes ARE comes from graph-layout.js, settling in a worker; this
   file never moves one. Nothing here touches the DOM outside its own two
   canvases: hovering, selecting, travelling and link mode are reported to the
   view through callbacks, and the view owns the card, the tip and the
   toolbar.

   Light is SCREENED, not added: a + b - ab is commutative, so nothing needs
   sorting by depth, and it saturates, so a core where a thousand memories
   overlap approaches white without reaching it and keeps its type colours.
   Both properties are load-bearing at scale -- addition clips, and a hundred
   thousand depth comparisons per frame buy an occlusion nobody looking at a
   star field expects. Distance is carried by fog: the far side of the cloud
   fades toward the page rather than crowding the near side. */

import { cssVar } from './core/dom.js';

/* Camera. The field of view is narrow for a 3D scene on purpose: perspective
   this gentle keeps the cloud's shape readable while still separating near
   from far, and a wide angle turns an orbit into a lurch. */
const FOV = 42 * Math.PI / 180;
const PITCH_MAX = Math.PI / 2 - .03;
/* Where a travel to one memory settles. Its relations rest around 78 units
   away (LINK_LEN in the layout), so this frames a memory together with
   everything it is joined to. */
const HOP_DIST = 240;
const DIST_MIN = 24;
/* how far past the framed radius a zoom-out may reach */
const DIST_SPAN = 3.4;
/* a travel, and the pull-back that frames the whole universe */
const FLY_MS = 720;
const FIT_MS = 900;
/* The distance free flight reads its speed and its throttle from. Both answer
   to the framing distance, so one key press covers a proportionate amount
   inside a cluster and across the store -- but they stop answering past this.
   The framing of a whole store is thousands of units out, and speed straight
   off that number opens the view at ten times the pace of a memory's own
   neighbourhood. */
const FLY_REACH = HOP_DIST * 2;
/* How fast the framing chases the arrangement while it is still settling.
   The camera is following a cloud that is growing, so it eases rather than
   snapping to each position update -- one movement instead of eleven jumps a
   second. Time constant in ms. */
const FOLLOW_TAU = 260;

/* Stars. The core radius in world units, by how many relations a memory
   holds, and the floor in screen pixels that keeps a distant one receding
   rather than vanishing. */
const R_BASE = 3.2;
const R_PER_LINK = 1.2;
const R_MAX_LINK = 6.5;
/* The screen size of a core, floor and ceiling, in CSS px. The floor keeps a
   distant memory receding rather than vanishing; the ceiling is what stops
   one from becoming a lamp when the camera arrives next to it -- perspective
   alone put a hub at 47px across and its halo at 70, which reads as a light
   leak and not as a memory. Degree still tells inside the ceiling, because a
   leaf is capped tighter than a hub. */
const MIN_PX = 1.7;
const MAX_PX = 11;
const MAX_PX_LEAF = .55;
/* Every radius below is in starPx units -- one is what starPx() returns, and
   the shader measures its own `d` in the same, so what the drawing calls the
   edge of a star and what a filament retreats to are the same number.

   STAR_QUAD has to cover the widest of them. It used to be 1.5 while the
   fragment normalised `d` by that same 1.5, which put the ring's outer edge
   outside the quad: the ring was clipped to four arcs near the diagonals and
   read as a bracket rather than a ring. */
const STAR_EDGE = 1.5;        /* the core's outer edge */
const STAR_SOLID = 1.05;      /* inside this the core is opaque */
const STAR_HOLE = 1.2;        /* an archived star is a ring around this */
const STAR_MARK_IN = 1.7;     /* a marked star's ring, inner and outer */
const STAR_MARK_OUT = 2.13;
const STAR_HALO = 2.25;       /* where the halo reaches zero */
const STAR_QUAD = 2.25;

/* A filament's half-width in CSS px, and what the selection's own are
   multiplied by. One pixel of that half-width is spent on the feather, so
   1.2 draws as a hairline with a soft edge and the hot ones come out around
   twice as wide -- the ratio the diagram canvas uses between a selected
   step's lines and the rest. */
const LINK_HALF = 1.2;
const LINK_HOT = 1.8;
/* Where a filament stops, in the same starPx units the shader measures in:
   clear of the core, and at the inner edge of the ring a marked star wears.
   Not at the halo's outer edge -- the halo is under a hundredth of an alpha
   out there, so stopping at it leaves a gap with nothing in it. Landing on
   the ring instead reads as one mark: the ring and the filaments a selection
   owns are the same colour. */
const LINK_CLEAR = STAR_MARK_IN;
const LINK_GAP = 1;

/* The idle life of the universe: a slow, tiny brightness wander. Nothing
   MOVES, which is the point -- the arrangement being read has to hold still,
   and a star field that is perfectly static reads as a screenshot. Off under
   prefers-reduced-motion, and then the loop stops at rest. */
const TWINKLE_HZ = .55;
const IDLE_FPS = 30;

/* What a memory fades to, and why there are two levels. The spotlight is a
   FILTER -- a miss is not part of the answer, so it goes right down. A
   selection is a FOCUS: everything more than one relation away is still
   context, and 0.3 is what the diagram canvas dims an unselected step's lines
   to, so the two surfaces read the same. Both at once takes the lower. */
const DIM = .16;
const FOCUS_DIM = .3;

/* Names. The pool is the most-connected memories, which is what a reader
   scanning a cloud is looking for; the cap is what fits before labels read as
   texture instead of text. */
const LABEL_POOL = 1600;
const LABEL_MAX = 34;
const LABEL_MIN_PX = 2.6;
const LABEL_FONT = 12;
const LABEL_CLIP = 30;
const LABEL_CLIP_LEAD = 60;
/* How many names the frame is allowed at the distance it is holding. A wide
   shot is for SHAPE -- where the clusters and the loose ends are -- and
   thirty names laid over it is a page of text with a drawing behind it. Names
   arrive as the reader comes in, and HOP_DIST is where all of them fit. */
const LABEL_MIN = 6;
/* the room a name claims around itself, so two of them never read as one
   line */
const LABEL_PAD_X = 5;
const LABEL_PAD_Y = 10;
/* the margin a name keeps from the frame, and the width one character
   averages at LABEL_FONT -- enough to cut the text before measuring it, which
   is what keeps a narrow canvas from clipping every label mid-word */
const LABEL_EDGE = 8;
const LABEL_EM = 6.2;

const SKY_STARS = 1400;

/* one instance of the star buffer: r, g, b, size, flags, dim, seed */
const STYLE_STRIDE = 7;

/* The same rule as GLSL_STAR_PX, for the pointer and the names; the
   constants are shared with it and only the language differs.

   The JavaScript side used to be `size * px / dist` with the floor and NO
   ceiling, so a star drawn 16px across answered a pointer 25px away and wore
   its name 30px out. Both errors grew as 1/dist: the closer the camera came,
   the further from a memory you could hover it. Measured at a middling zoom,
   a star kept answering across 50px of a row it is drawn 33px wide in. */
const starPx = (size, dist, px) => {
  const cap = MAX_PX * (MAX_PX_LEAF + (1 - MAX_PX_LEAF)
    * Math.min(1, Math.max(0, (size - R_BASE) / R_MAX_LINK)));
  return Math.min(cap, Math.max(MIN_PX, size * px / dist));
};

/* --------------------------------------------------------------- mat4 */

const mat4 = () => new Float32Array(16);

function perspective(out, fov, aspect, near, far) {
  const f = 1 / Math.tan(fov / 2);
  out.fill(0);
  out[0] = f / aspect; out[5] = f; out[11] = -1;
  out[10] = (far + near) / (near - far);
  out[14] = 2 * far * near / (near - far);
  return out;
}

/* out = a * b, column-major. `out` must not be `a` or `b`. */
function mul(out, a, b) {
  for (let c = 0; c < 4; c++) {
    const b0 = b[c * 4], b1 = b[c * 4 + 1], b2 = b[c * 4 + 2], b3 = b[c * 4 + 3];
    for (let r = 0; r < 4; r++) {
      out[c * 4 + r] = a[r] * b0 + a[4 + r] * b1 + a[8 + r] * b2 + a[12 + r] * b3;
    }
  }
  return out;
}

/* the ease every motion in this file uses: already moving when it starts, and
   arriving rather than stopping */
const ease = t => 1 - Math.pow(1 - t, 3);

/* '#bb86fc' or 'rgba(...)' as three 0..1 floats, since cssVar hands back
   whichever form the stylesheet happens to hold. */
function rgb(css, fallback) {
  const s = String(css || '').trim();
  let m = /^#([\da-f]{6})$/i.exec(s);
  if (m) {
    const v = parseInt(m[1], 16);
    return [(v >> 16 & 255) / 255, (v >> 8 & 255) / 255, (v & 255) / 255];
  }
  m = /^#([\da-f]{3})$/i.exec(s);
  if (m) {
    const v = parseInt(m[1], 16);
    return [(v >> 8 & 15) * 17 / 255, (v >> 4 & 15) * 17 / 255, (v & 15) * 17 / 255];
  }
  m = /rgba?\(([^)]+)\)/i.exec(s);
  if (m) {
    const p = m[1].split(/[,\s/]+/).filter(Boolean).map(Number);
    return [(p[0] || 0) / 255, (p[1] || 0) / 255, (p[2] || 0) / 255];
  }
  return fallback;
}

/* --------------------------------------------------------------- shaders */

/* How wide a memory is on screen, in pixels: the floor keeps a distant one
   receding rather than vanishing, and the ceiling -- which a leaf gets less
   of than a hub -- stops a near one becoming a lamp.

   THREE readers, and they have to agree. Two are shader programs: the star
   sizes its quad by it, the filament stops at the disc it names. The third is
   `starPx` in JavaScript below, which the pointer and the labels read. Reads
   `u_px`, which both programs declare. */
const GLSL_STAR_PX = `
float starPx(float size, float dist, float minPx, float maxPx) {
  float cap = maxPx * mix(${MAX_PX_LEAF.toFixed(2)}, 1.0,
    clamp((size - ${R_BASE.toFixed(2)}) / ${R_MAX_LINK.toFixed(2)}, 0.0, 1.0));
  return clamp(size * u_px / dist, minPx, cap);
}`;

const V_STAR = `#version 300 es
in vec2 a_corner;
in vec3 a_center;
in vec3 a_color;
in float a_size;
in float a_flags;
in float a_dim;
in float a_seed;
uniform mat4 u_view;
uniform mat4 u_proj;
uniform float u_px;
uniform float u_minPx;
uniform float u_maxPx;
uniform vec2 u_fog;
out vec2 v_uv;
out vec3 v_color;
out float v_flags;
out float v_dim;
out float v_seed;
out float v_fade;
${GLSL_STAR_PX}
void main() {
  /* billboarding in EYE space: an offset on x and y there always faces the
     camera, with no basis vectors to pass in and nothing to keep in step */
  vec4 eye = u_view * vec4(a_center, 1.0);
  float dist = max(0.001, -eye.z);
  /* Far away it stops shrinking, so a wide shot of the whole store is a sky
     rather than an empty frame; close up it stops growing. */
  float r = starPx(a_size, dist, u_minPx, u_maxPx) * dist / u_px;
  eye.xy += a_corner * r;
  v_uv = a_corner;
  v_color = a_color;
  v_flags = a_flags;
  v_dim = a_dim;
  v_seed = a_seed;
  v_fade = 1.0 - smoothstep(u_fog.x, u_fog.y, dist);
  gl_Position = u_proj * eye;
}`;

const F_STAR = `#version 300 es
precision highp float;
in vec2 v_uv;
in vec3 v_color;
in float v_flags;
in float v_dim;
in float v_seed;
in float v_fade;
uniform float u_time;
uniform float u_twinkle;
uniform vec3 u_accent;
out vec4 frag;
bool bit(float flags, float b) { return mod(floor(flags / b), 2.0) >= 1.0; }
void main() {
  if (v_fade <= 0.002) discard;
  bool diamond = bit(v_flags, 1.0);
  bool hollow  = bit(v_flags, 2.0);
  bool marked  = bit(v_flags, 4.0);
  bool dashed  = bit(v_flags, 8.0);
  /* the diamond is the secondary encoding for anti-patterns: the red of that
     type and the green of a handoff are the pair a red-green deficiency
     cannot separate, so one of them is not a disc */
  float d = diamond ? abs(v_uv.x) + abs(v_uv.y) : length(v_uv);
  float tw = 1.0 + u_twinkle * 0.15 * sin(u_time * (0.7 + fract(v_seed)) + v_seed * 6.2831);
  vec3 col = v_color * tw;
  /* archived is a ring: the memory is still there and it is not filled in */
  float core = hollow
    ? smoothstep(${(STAR_EDGE + 0.05).toFixed(2)}, ${(STAR_EDGE - 0.15).toFixed(2)}, d)
      * smoothstep(${(STAR_HOLE - 0.3).toFixed(2)}, ${STAR_HOLE.toFixed(2)}, d)
    : smoothstep(${STAR_EDGE.toFixed(2)}, ${STAR_SOLID.toFixed(2)}, d);
  float halo = pow(max(0.0, 1.0 - d / ${STAR_HALO.toFixed(2)}), 3.0) * 0.34;
  /* The halo fades faster than the core -- v_dim twice over. A memory pushed
     to context keeps a legible point and loses its bloom, so a few hundred of
     them read as a star field behind the focus rather than as a haze over it. */
  float a = (core + halo * (hollow ? 0.5 : 1.0) * v_dim) * v_dim * v_fade;
  if (marked) {
    float ring = smoothstep(${STAR_MARK_OUT.toFixed(2)}, ${(STAR_MARK_OUT - 0.18).toFixed(2)}, d)
      * smoothstep(${STAR_MARK_IN.toFixed(2)}, ${(STAR_MARK_IN + 0.18).toFixed(2)}, d);
    if (dashed && fract(atan(v_uv.y, v_uv.x) * 2.2) > 0.55) ring = 0.0;
    col = mix(col, u_accent, ring);
    a += ring * 0.95 * v_fade;
  }
  if (a < 0.004) discard;
  frag = vec4(col * a, a);
}`;

const V_LINK = `#version 300 es
in vec2 a_corner;
in vec3 a_from;
in vec3 a_to;
in vec4 a_style;
uniform mat4 u_view;
uniform mat4 u_proj;
uniform float u_px;
uniform float u_minPx;
uniform float u_maxPx;
uniform vec4 u_width;
uniform vec2 u_fog;
out float v_t;
out float v_alpha;
out float v_hot;
out float v_fade;
out float v_edge;
out float v_half;
${GLSL_STAR_PX}
void main() {
  /* A filament is a RIBBON, not a line primitive: gl.lineWidth is clamped to
     one device pixel on every desktop browser, which on a 2x panel is half a
     CSS pixel of hard-edged staircase. The ribbon is turned to face the
     camera in eye space, the same billboard a star uses, so nothing has to
     divide by w and an endpoint behind the camera still clips normally. */
  vec3 ea = (u_view * vec4(a_from, 1.0)).xyz;
  vec3 eb = (u_view * vec4(a_to, 1.0)).xyz;
  /* A relation joins two memories; it does not cross them. Each end retreats
     to the edge of its own star's disc, by the same rule the star is drawn
     with, plus a gap. Capped at a share of the span, or two memories closer
     together than their own radii would turn the ribbon inside out. */
  vec3 span = eb - ea;
  float reach = length(span);
  vec3 along = reach > 1e-5 ? span / reach : vec3(0.0, 0.0, 1.0);
  float da = max(0.001, -ea.z), db = max(0.001, -eb.z);
  float ra = (starPx(a_style.z, da, u_minPx, u_maxPx) * u_width.w + u_width.z) * da / u_px;
  float rb = (starPx(a_style.w, db, u_minPx, u_maxPx) * u_width.w + u_width.z) * db / u_px;
  float room = reach * 0.42;
  ea += along * min(ra, room);
  eb -= along * min(rb, room);
  vec3 e = mix(ea, eb, a_corner.x);
  float dist = max(0.001, -e.z);
  vec3 nrm = cross(eb - ea, e);
  float side = length(nrm);
  /* a filament pointing straight at the camera has no perpendicular to
     offset along; it is a point, and any direction will do */
  nrm = side > 1e-5 ? nrm / side : vec3(1.0, 0.0, 0.0);
  float half_px = u_width.x * mix(1.0, u_width.y, a_style.y);
  e += nrm * (a_corner.y * half_px * dist / u_px);
  v_edge = a_corner.y;
  v_half = half_px;
  v_t = a_corner.x;
  v_alpha = a_style.x;
  v_hot = a_style.y;
  v_fade = 1.0 - smoothstep(u_fog.x, u_fog.y, dist);
  gl_Position = u_proj * vec4(e, 1.0);
}`;

const F_LINK = `#version 300 es
precision mediump float;
in float v_t;
in float v_alpha;
in float v_hot;
in float v_fade;
in float v_edge;
in float v_half;
uniform vec3 u_color;
uniform vec3 u_accent;
out vec4 frag;
void main() {
  /* Direction, without an arrowhead: a filament is faint where the relation
     starts and bright where it points. An arrowhead seen at an angle in
     perspective is three pixels that read as nothing. */
  float a = v_alpha * mix(0.14, 0.85, v_t) * v_fade;
  /* a relation the selection owns: the accent colour, brighter, and wider --
     the same three signals the diagram canvas gives a selected step's lines */
  a *= 1.0 + v_hot * 1.4;
  /* The antialiasing, and it is the ribbon's own: distance from the edge in
     PIXELS, so the feather is one pixel wide whatever the width or the zoom.
     Nothing here needs the framebuffer to have samples to spare. */
  a *= clamp((1.0 - abs(v_edge)) * v_half, 0.0, 1.0);
  if (a < 0.003) discard;
  frag = vec4(mix(u_color, u_accent, v_hot) * a, a);
}`;

const V_SKY = `#version 300 es
in vec3 a_dir;
in float a_seed;
uniform mat4 u_sky;
uniform float u_radius;
uniform float u_dpr;
out float v_seed;
void main() {
  gl_Position = u_sky * vec4(a_dir * u_radius, 1.0);
  gl_PointSize = (1.0 + fract(a_seed * 7.13) * 1.7) * u_dpr;
  v_seed = a_seed;
}`;

const F_SKY = `#version 300 es
precision mediump float;
in float v_seed;
uniform float u_time;
uniform float u_twinkle;
uniform vec3 u_color;
out vec4 frag;
void main() {
  float d = length(gl_PointCoord - 0.5) * 2.0;
  float a = smoothstep(1.0, 0.1, d) * (0.05 + fract(v_seed * 3.71) * 0.14);
  a *= 1.0 + u_twinkle * 0.35 * sin(u_time * 0.9 + v_seed * 12.9);
  frag = vec4(u_color * a, a);
}`;

/* --------------------------------------------------------------- engine */

export class Universe {
  /* `nodes` and `edges` are the /api/graph payload; `colorOf(type)` hands
     back the CSS colour of a memory type, because the type palette belongs to
     the app's shared vocabulary and not to a renderer.

     Throws when WebGL2 is missing, which is the caller's cue to say so.

     The callbacks are the whole outward surface: onSelect(node) when the
     selection changes, onOpen(node) when the reader asks for the record,
     onHover(node, x, y) for the tip, onLink(kind, a, b) for link mode
     ('from' or 'pair'), onSettle(progress, done, error) for the arrangement,
     onFlight(on) when free flight is entered or left by keyboard, and
     obstacles() for the boxes the chrome occupies, in canvas coordinates, so
     no name is drawn where a panel is about to cover it. */
  constructor(glCanvas, labelCanvas, {
    nodes, edges, colorOf,
    onSelect = () => {}, onOpen = () => {}, onHover = () => {},
    onLink = () => {}, onSettle = () => {}, onFlight = () => {},
    obstacles = () => [],
  }) {
    /* No powerPreference. Asking for 'high-performance' asks a machine with
       switchable graphics for its discrete GPU, and a browser that cannot
       hand one over -- on battery, in a power-saving mode, with the card
       asleep -- answers by creating no context at all. A few hundred stars do
       not need it, and a view that draws on the integrated GPU beats a view
       that does not draw.

       The browser's own reason for refusing arrives on an event rather than
       as a return value, and it is the only thing that tells a reader whether
       this is a driver, a setting, or too many pages already drawing. */
    let refused = '';
    glCanvas.addEventListener('webglcontextcreationerror',
      e => { refused = e.statusMessage || ''; }, { once: true });
    const gl = glCanvas.getContext('webgl2', {
      alpha: true, antialias: false, premultipliedAlpha: true, depth: false,
    });
    if (!gl) {
      throw Object.assign(new Error(refused || 'getContext("webgl2") returned null'),
                          { kind: 'context' });
    }
    this.gl = gl;
    this.cv = glCanvas;
    this.lv = labelCanvas;
    this.lx = labelCanvas.getContext('2d');
    this.cb = { onSelect, onOpen, onHover, onLink, onSettle, onFlight, obstacles };

    this.nodes = nodes.map((n, i) => ({
      ...n,
      i,
      /* the name: the title a writer chose, falling back to the opening line
         of the body the way a memory row does */
      name: (n.title || '').trim() || n.label || n.uid,
      size: R_BASE + Math.min(R_MAX_LINK, (n.degree || 0) * R_PER_LINK),
      /* the spotlight's verdict on this memory, kept apart from the
         selection's focus: see _fade */
      miss: false,
    }));
    this.byUid = Object.fromEntries(this.nodes.map(n => [n.uid, n]));
    this.edges = edges.filter(e => this.byUid[e.from_uid] && this.byUid[e.to_uid]);
    this.adj = Object.fromEntries(this.nodes.map(n => [n.uid, []]));
    for (const e of this.edges) {
      this.adj[e.from_uid].push(e.to_uid);
      this.adj[e.to_uid].push(e.from_uid);
    }

    const n = this.nodes.length;
    this.pos = new Float32Array(n * 3);
    /* x, y in CSS px, radius in px, distance from the camera -- or a negative
       distance for a memory behind it. Labels and hit testing both read it. */
    this.scr = new Float32Array(n * 4);
    /* What keeps it honest is a pair of counters, not a dirty flag. A camera
       change bumps camAt, an arrangement bumps posAt, and the projection
       records the pair it was computed from -- so a path that changes the
       camera cannot hand a reader stale screen coordinates by forgetting to
       invalidate anything. Every one of them forgot: a wheel, a pan, an orbit
       and each frame of a flight set camDirty alone, and _project() returned
       on a clean flag before applying the pending camera. What that produces
       is a pointer answered by where a star USED to be. */
    this.camAt = 0; this.posAt = 0;
    this.scrCamAt = -1; this.scrPosAt = -1;
    this.radius = 400;
    this.settled = false;

    this.target = [0, 0, 0];
    this.yaw = .62; this.pitch = .3; this.dist = 900;
    this.view = mat4(); this.proj = mat4(); this.sky = mat4(); this.rot = mat4();
    this.fwd = [0, 0, -1]; this.right = [1, 0, 0]; this.up = [0, 1, 0];
    this.eye = [0, 0, 900];
    this.near = 1; this.far = 4000;
    this.fog = [900, 2000];
    this.pxScale = 600;
    this.tween = null; this.follow = null;
    this.camDirty = true;

    this.hover = null; this.selected = null; this.cameFrom = null;
    /* the selection and everything one relation from it, by uid, or null for
       no selection at all */
    this.focusSet = null;
    this.linkMode = false; this.linkFrom = null;
    this.orbit = null; this.panning = null; this.moved = false;
    this.pointers = new Map(); this.pinch = null;
    this.flight = false; this.flightHeld = false;
    this.keys = new Set();
    this.lastFrame = 0;

    this.motion = !matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.running = true;
    this.raf = 0;
    this.dirty = true;

    this.colors = {
      edge: rgb(cssVar('--canvas-edge'), [1, 1, 1]),
      accent: rgb(cssVar('--accent'), [.73, .53, .99]),
      sky: rgb(cssVar('--ink-2'), [1, 1, 1]),
      ink: cssVar('--ink') || 'rgba(255,255,255,.87)',
      lead: cssVar('--accent-hi') || '#d3b1ff',
      font: cssVar('--font-ui') || 'Roboto, sans-serif',
    };
    /* A name sits on a plate of the page's own ground. What is behind it is a
       star field, which over the middle of a large store is near white: no
       outline around a glyph survives that, only an opaque backing. */
    const ground = rgb(cssVar('--bg'), [.07, .07, .07]).map(v => Math.round(v * 255));
    this.colors.plate = `rgba(${ground.join(', ')}, .8)`;
    this.typeColor = Object.create(null);
    for (const node of this.nodes) {
      if (!this.typeColor[node.type]) {
        this.typeColor[node.type] = rgb(colorOf(node.type), [.62, .62, .62]);
      }
    }

    /* bound before anything can ask for a frame */
    this._loop = this._loop.bind(this);
    this._buildGL();
    this._labelPool();

    /* Two triggers, the same pair the diagram canvas uses. The frame changes
       size without the window ever resizing -- the rail collapses, a
       scrollbar appears -- and a window-only listener misses it; a missed
       resize leaves the backing store at its old size, which the stylesheet
       hides by scaling the image and hit testing does not, because the
       pointer then arrives in a coordinate space the projection is not in.
       _local() is the third: it reads the frame anyway, so it is where a
       divergence is caught for free. */
    this._resize = () => this.resize();
    addEventListener('resize', this._resize);
    if (typeof ResizeObserver === 'function') {
      this._ro = new ResizeObserver(this._resize);
      this._ro.observe(glCanvas.parentElement);
    }
    this.resize();

    /* Pointer events, not mouse events: one path serves a mouse, a pen and a
       finger, which is what the flat canvas ended up doing too. */
    glCanvas.addEventListener('pointerdown', e => this._down(e));
    glCanvas.addEventListener('pointermove', e => this._move(e));
    glCanvas.addEventListener('wheel', e => this._wheel(e), { passive: false });
    glCanvas.addEventListener('click', e => this._click(e));
    glCanvas.addEventListener('contextmenu', e => e.preventDefault());
    glCanvas.addEventListener('webglcontextlost', e => {
      e.preventDefault();
      this.running = false;
      this.cb.onSettle(1, true, 'context lost');
    });
    this._up = e => this._pointerUp(e);
    addEventListener('pointerup', this._up);
    addEventListener('pointercancel', this._up);
    this._keyDown = e => this._onKeyDown(e);
    this._keyUp = e => this._onKeyUp(e);
    addEventListener('keydown', this._keyDown);
    addEventListener('keyup', this._keyUp);
    this._blur = () => { this.keys.clear(); if (this.flightHeld) this.setFlight(false); };
    addEventListener('blur', this._blur);

    this._startLayout();
    this._wake();
  }

  destroy() {
    this.running = false;
    cancelAnimationFrame(this.raf);
    this.raf = 0;
    if (this.worker) {
      this.worker.postMessage({ t: 'stop' });
      this.worker.terminate();
      this.worker = null;
    }
    removeEventListener('resize', this._resize);
    this._ro?.disconnect();
    removeEventListener('pointerup', this._up);
    removeEventListener('pointercancel', this._up);
    removeEventListener('keydown', this._keyDown);
    removeEventListener('keyup', this._keyUp);
    removeEventListener('blur', this._blur);
    /* Hand the buffers back before dropping the context. Losing it should
       free them anyway; a browser keeps a small number of contexts alive at
       once, and this view is entered and left often enough that it is worth
       not relying on that. */
    const gl = this.gl;
    for (const p of [this.pStar, this.pLink, this.pSky]) if (p) gl.deleteProgram(p.p);
    for (const v of [this.starVAO, this.linkVAO, this.skyVAO]) if (v) gl.deleteVertexArray(v);
    for (const b of [this.posBuf, this.styleBuf, this.linkPosBuf, this.linkSBuf]) {
      if (b) gl.deleteBuffer(b);
    }
    gl.getExtension('WEBGL_lose_context')?.loseContext();
  }

  /* --------------------------------------------------------- the layout */

  _startLayout() {
    const n = this.nodes.length;
    if (!n) {
      this.settled = true;
      this.cb.onSettle(1, true);
      return;
    }
    const degree = new Int32Array(n);
    for (const node of this.nodes) degree[node.i] = node.degree || 0;
    const ea = new Int32Array(this.edges.length);
    const eb = new Int32Array(this.edges.length);
    this.edges.forEach((e, k) => {
      ea[k] = this.byUid[e.from_uid].i;
      eb[k] = this.byUid[e.to_uid].i;
    });
    this.worker = new Worker(new URL('./graph-layout.worker.js', import.meta.url),
                             { type: 'module' });
    this.worker.onmessage = e => this._fromWorker(e.data);
    this.worker.onerror = () => {
      this.settled = true;
      this.cb.onSettle(1, true, 'worker');
    };
    this.worker.postMessage({ t: 'start', count: n, degree, ea, eb },
                            [degree.buffer, ea.buffer, eb.buffer]);
  }

  _fromWorker(msg) {
    if (msg.t === 'fail') {
      this.settled = true;
      this.cb.onSettle(1, true, msg.msg);
      return;
    }
    if (msg.t !== 'pos') return;
    this.pos.set(msg.pos.subarray(0, this.pos.length));
    this.settled = !!msg.done;
    this._uploadPositions();
    /* A universe still condensing is framed as it grows: the pull-back and
       the arrangement are one movement, which is this view's one authored
       moment. The framing eases rather than snapping, or eleven position
       updates a second would read as eleven jumps. */
    const [center, dist] = this._framing();
    if (this.settled) {
      this.follow = null;
      this._goTo(center, dist, FIT_MS);
    } else {
      this.follow = { target: center, dist };
    }
    this.dirty = true;
    this._wake();
    this.cb.onSettle(msg.progress, this.settled);
  }

  /* --------------------------------------------------------------- GL */

  _program(vs, fs) {
    const gl = this.gl;
    const compile = (type, src) => {
      const sh = gl.createShader(type);
      gl.shaderSource(sh, src);
      gl.compileShader(sh);
      if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
        throw new Error(gl.getShaderInfoLog(sh) || 'shader');
      }
      return sh;
    };
    const p = gl.createProgram();
    gl.attachShader(p, compile(gl.VERTEX_SHADER, vs));
    gl.attachShader(p, compile(gl.FRAGMENT_SHADER, fs));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
      throw new Error(gl.getProgramInfoLog(p) || 'link');
    }
    const u = {};
    for (let i = 0; i < gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS); i++) {
      const name = gl.getActiveUniform(p, i).name;
      u[name] = gl.getUniformLocation(p, name);
    }
    return { p, u };
  }

  _buildGL() {
    const gl = this.gl;
    gl.disable(gl.DEPTH_TEST);
    gl.enable(gl.BLEND);
    /* Screen, premultiplied: dst + src * (1 - dst). Commutative, so a
       hundred thousand stars skip the depth sort, and saturating, so a
       thousand overlapping halos brighten toward white without clamping. */
    gl.blendFuncSeparate(gl.ONE_MINUS_DST_COLOR, gl.ONE,
                         gl.ONE_MINUS_DST_ALPHA, gl.ONE);
    gl.clearColor(0, 0, 0, 0);

    this.pStar = this._program(V_STAR, F_STAR);
    this.pLink = this._program(V_LINK, F_LINK);
    this.pSky = this._program(V_SKY, F_SKY);

    const n = this.nodes.length;
    const F = Float32Array.BYTES_PER_ELEMENT;

    /* one quad, instanced once per memory */
    const quad = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, quad);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
      -STAR_QUAD, -STAR_QUAD, STAR_QUAD, -STAR_QUAD,
      -STAR_QUAD, STAR_QUAD, STAR_QUAD, STAR_QUAD,
    ]), gl.STATIC_DRAW);

    this.posBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.posBuf);
    gl.bufferData(gl.ARRAY_BUFFER, this.pos, gl.DYNAMIC_DRAW);

    this.style = new Float32Array(n * STYLE_STRIDE);
    this.styleBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.styleBuf);
    gl.bufferData(gl.ARRAY_BUFFER, this.style, gl.DYNAMIC_DRAW);

    this.starVAO = gl.createVertexArray();
    gl.bindVertexArray(this.starVAO);
    const sa = name => gl.getAttribLocation(this.pStar.p, name);
    gl.bindBuffer(gl.ARRAY_BUFFER, quad);
    gl.enableVertexAttribArray(sa('a_corner'));
    gl.vertexAttribPointer(sa('a_corner'), 2, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.posBuf);
    gl.enableVertexAttribArray(sa('a_center'));
    gl.vertexAttribPointer(sa('a_center'), 3, gl.FLOAT, false, 0, 0);
    gl.vertexAttribDivisor(sa('a_center'), 1);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.styleBuf);
    for (const [name, size, at] of [['a_color', 3, 0], ['a_size', 1, 3],
                                    ['a_flags', 1, 4], ['a_dim', 1, 5],
                                    ['a_seed', 1, 6]]) {
      const loc = sa(name);
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, size, gl.FLOAT, false, STYLE_STRIDE * F, at * F);
      gl.vertexAttribDivisor(loc, 1);
    }

    /* Relations: one instance each, over a four-vertex strip. `a_corner.x`
       picks the endpoint -- 0 at the source, 1 at the target, which is also
       the ramp the fragment reads for direction -- and `a_corner.y` picks the
       side of the ribbon. */
    const m = this.edges.length;
    this.linkPos = new Float32Array(m * 6);
    /* four floats per relation: how lit the filament is, whether it is one of
       the selection's own, and the size of the star at each end -- which is
       what the ribbon retreats to instead of crossing */
    this.linkStyle = new Float32Array(m * 4);
    this.edges.forEach((e, k) => {
      const o = k * 4;
      this.linkStyle[o] = 1;
      this.linkStyle[o + 2] = this.byUid[e.from_uid].size;
      this.linkStyle[o + 3] = this.byUid[e.to_uid].size;
    });

    const ribbon = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, ribbon);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
      0, -1, 0, 1, 1, -1, 1, 1,
    ]), gl.STATIC_DRAW);
    this.linkPosBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.linkPosBuf);
    gl.bufferData(gl.ARRAY_BUFFER, this.linkPos, gl.DYNAMIC_DRAW);
    this.linkSBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.linkSBuf);
    gl.bufferData(gl.ARRAY_BUFFER, this.linkStyle, gl.DYNAMIC_DRAW);

    this.linkVAO = gl.createVertexArray();
    gl.bindVertexArray(this.linkVAO);
    const la = name => gl.getAttribLocation(this.pLink.p, name);
    gl.bindBuffer(gl.ARRAY_BUFFER, ribbon);
    gl.enableVertexAttribArray(la('a_corner'));
    gl.vertexAttribPointer(la('a_corner'), 2, gl.FLOAT, false, 0, 0);
    /* both endpoints out of one buffer: the arrangement writes them as six
       floats per relation, which is a stride of 24 with the target at 12 */
    gl.bindBuffer(gl.ARRAY_BUFFER, this.linkPosBuf);
    for (const [name, at] of [['a_from', 0], ['a_to', 3]]) {
      const loc = la(name);
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, 3, gl.FLOAT, false, 6 * F, at * F);
      gl.vertexAttribDivisor(loc, 1);
    }
    gl.bindBuffer(gl.ARRAY_BUFFER, this.linkSBuf);
    gl.enableVertexAttribArray(la('a_style'));
    gl.vertexAttribPointer(la('a_style'), 4, gl.FLOAT, false, 0, 0);
    gl.vertexAttribDivisor(la('a_style'), 1);

    /* the sky: a fixed shell of far dust, so an orbit has parallax to read */
    const dir = new Float32Array(SKY_STARS * 3), seed = new Float32Array(SKY_STARS);
    for (let i = 0; i < SKY_STARS; i++) {
      const y = 1 - 2 * (i + .5) / SKY_STARS;
      const ring = Math.sqrt(Math.max(0, 1 - y * y));
      const a = i * 2.399963;
      dir[i * 3] = Math.cos(a) * ring;
      dir[i * 3 + 1] = y;
      dir[i * 3 + 2] = Math.sin(a) * ring;
      seed[i] = (i * 0.6180339887) % 1;
    }
    this.skyVAO = gl.createVertexArray();
    gl.bindVertexArray(this.skyVAO);
    for (const [data, name, size] of [[dir, 'a_dir', 3], [seed, 'a_seed', 1]]) {
      const buf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
      const loc = gl.getAttribLocation(this.pSky.p, name);
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0);
    }

    gl.bindVertexArray(null);
    this._uploadStyle();
  }

  _uploadPositions() {
    const gl = this.gl;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.posBuf);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, this.pos);
    const lp = this.linkPos, pos = this.pos;
    this.edges.forEach((e, k) => {
      const a = this.byUid[e.from_uid].i * 3, b = this.byUid[e.to_uid].i * 3, o = k * 6;
      lp[o] = pos[a]; lp[o + 1] = pos[a + 1]; lp[o + 2] = pos[a + 2];
      lp[o + 3] = pos[b]; lp[o + 4] = pos[b + 1]; lp[o + 5] = pos[b + 2];
    });
    gl.bindBuffer(gl.ARRAY_BUFFER, this.linkPosBuf);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, lp);
    this.posAt++;
  }

  /* How lit a memory is: 1, or the lower of whatever the spotlight and the
     selection have to say about it. A selection focuses -- itself and one
     relation out stay at full strength and the rest of the store drops to
     context -- and a spotlight miss drops further, because that one is a
     filter and not a focus. */
  _fade(node) {
    let v = node.miss ? DIM : 1;
    if (this.focusSet && !this.focusSet.has(node.uid)) v = Math.min(v, FOCUS_DIM);
    return v;
  }

  /* One memory's colour, size and state bits. The bits are: 1 diamond
     (anti-pattern), 2 hollow (archived), 4 marked (hovered, selected, or the
     source of a link), 8 dashed (that source specifically). */
  _writeStyle(node) {
    const s = this.style, o = node.i * STYLE_STRIDE;
    const c = this.typeColor[node.type] || [.62, .62, .62];
    s[o] = c[0]; s[o + 1] = c[1]; s[o + 2] = c[2];
    s[o + 3] = node.size;
    s[o + 4] = (node.type === 'anti_pattern' ? 1 : 0)
      + (node.status === 'archived' ? 2 : 0)
      + (node === this.selected || node === this.hover || node === this.linkFrom ? 4 : 0)
      + (node === this.linkFrom ? 8 : 0);
    s[o + 5] = this._fade(node);
    s[o + 6] = (node.i * 0.6180339887) % 1;
  }

  /* One memory changed state. Hover fires on every pointer move that crosses
     a star, so rewriting the whole instance buffer for it would put the
     store's size on the cost of moving the mouse. */
  _touch(node) {
    if (!node) return;
    this._writeStyle(node);
    const gl = this.gl, o = node.i * STYLE_STRIDE;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.styleBuf);
    gl.bufferSubData(gl.ARRAY_BUFFER, o * Float32Array.BYTES_PER_ELEMENT,
                     this.style.subarray(o, o + STYLE_STRIDE));
    this.dirty = true;
    this._wake();
  }

  /* Every memory, and every filament's alpha with them: for a spotlight,
     which is a keystroke and not a frame. */
  _uploadStyle() {
    const gl = this.gl;
    for (const node of this.nodes) this._writeStyle(node);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.styleBuf);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, this.style);

    /* A filament is only as lit as its dimmer end: a match keeps the
       relations that reach it, so what it is joined to stays visible. One the
       selection owns is hot instead -- both its ends are in the focus, so
       there is nothing dimming it. */
    /* only the first two of the four move: the sizes at the ends are the
       arrangement's, not the selection's */
    const st = this.linkStyle, sel = this.selected;
    this.edges.forEach((e, k) => {
      const a = this.byUid[e.from_uid], b = this.byUid[e.to_uid];
      st[k * 4] = Math.min(this._fade(a), this._fade(b));
      st[k * 4 + 1] = sel && (a === sel || b === sel) ? 1 : 0;
    });
    gl.bindBuffer(gl.ARRAY_BUFFER, this.linkSBuf);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, st);
    this.dirty = true;
    this._wake();
  }

  resize() {
    const r = this.cv.parentElement.getBoundingClientRect();
    const dpr = Math.min(2, devicePixelRatio || 1);
    this.w = Math.max(1, r.width); this.h = Math.max(1, r.height);
    this.dpr = dpr;
    for (const cv of [this.cv, this.lv]) {
      cv.width = Math.round(this.w * dpr);
      cv.height = Math.round(this.h * dpr);
    }
    this.gl.viewport(0, 0, this.cv.width, this.cv.height);
    this.lx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.camDirty = true;
    this.dirty = true;
    this._wake();
  }

  /* ------------------------------------------------------------- camera */

  _camera() {
    const cp = Math.cos(this.pitch), sp = Math.sin(this.pitch);
    const cy = Math.cos(this.yaw), sy = Math.sin(this.yaw);
    /* yaw about world Y, then pitch: at 0,0 the camera looks down -Z */
    const f = [-sy * cp, sp, -cy * cp];
    const right = [cy, 0, -sy];
    const up = [sy * sp, cp, cy * sp];
    const eye = [
      this.target[0] - f[0] * this.dist,
      this.target[1] - f[1] * this.dist,
      this.target[2] - f[2] * this.dist,
    ];
    this.fwd = f; this.right = right; this.up = up; this.eye = eye;

    const v = this.view;
    v[0] = right[0]; v[4] = right[1]; v[8] = right[2];
    v[1] = up[0]; v[5] = up[1]; v[9] = up[2];
    v[2] = -f[0]; v[6] = -f[1]; v[10] = -f[2];
    v[3] = 0; v[7] = 0; v[11] = 0; v[15] = 1;
    v[12] = -(right[0] * eye[0] + right[1] * eye[1] + right[2] * eye[2]);
    v[13] = -(up[0] * eye[0] + up[1] * eye[1] + up[2] * eye[2]);
    v[14] = f[0] * eye[0] + f[1] * eye[1] + f[2] * eye[2];

    this.near = Math.max(.5, this.dist * .01);
    this.far = this.dist + this.radius * 4 + 400;
    perspective(this.proj, FOV, this.w / this.h, this.near, this.far);

    /* the sky turns with the camera and does not travel with it */
    this.rot.set(v);
    this.rot[12] = this.rot[13] = this.rot[14] = 0;
    mul(this.sky, this.proj, this.rot);

    /* CSS pixels per world unit at one unit of distance */
    this.pxScale = .5 * this.h / Math.tan(FOV / 2);
    /* Fog opens where the camera is holding and closes behind the cloud, so
       what the reader is looking at is at full strength and the far side
       recedes instead of competing with it. */
    this.fog = [this.dist * .9, this.dist + this.radius * 1.9 + 200];
    this.camDirty = false;
    this.camAt++;
  }

  /* The bounding sphere of the arrangement, and the distance that holds it. */
  _framing() {
    const n = this.nodes.length;
    if (!n) return [[0, 0, 0], this.dist];
    let cx = 0, cy = 0, cz = 0;
    for (let i = 0; i < n; i++) {
      cx += this.pos[i * 3]; cy += this.pos[i * 3 + 1]; cz += this.pos[i * 3 + 2];
    }
    cx /= n; cy /= n; cz /= n;
    let r2 = 0;
    for (let i = 0; i < n; i++) {
      const dx = this.pos[i * 3] - cx;
      const dy = this.pos[i * 3 + 1] - cy;
      const dz = this.pos[i * 3 + 2] - cz;
      const d2 = dx * dx + dy * dy + dz * dz;
      if (d2 > r2) r2 = d2;
    }
    this.radius = Math.max(60, Math.sqrt(r2));
    return [[cx, cy, cz], Math.max(DIST_MIN, this.radius / Math.tan(FOV / 2) * 1.05 + 40)];
  }

  fit(animate = true) {
    const [center, dist] = this._framing();
    this.follow = null;
    this._goTo(center, dist, animate ? FIT_MS : 0);
  }

  /* Travel to one memory: it ends up in the middle, at a distance that frames
     what it is joined to. Never pulls BACK -- a reader already close in asked
     to move sideways, not to zoom out. */
  flyTo(uid, ms = FLY_MS) {
    const node = this.byUid[uid];
    if (!node) return;
    const i = node.i * 3;
    this.follow = null;
    this._goTo([this.pos[i], this.pos[i + 1], this.pos[i + 2]],
               Math.max(DIST_MIN, Math.min(this.dist, HOP_DIST)), ms);
  }

  _goTo(target, dist, ms) {
    if (!ms || !this.motion) {
      this.target = target.slice();
      this.dist = dist;
      this.tween = null;
      this.camDirty = true;
      this.dirty = true;
      this._wake();
      return;
    }
    this.tween = {
      at: performance.now(), ms,
      from: { target: this.target.slice(), dist: this.dist },
      to: { target: target.slice(), dist },
    };
    this._wake();
  }

  /* The tween, then the follow: a travel the reader asked for outranks the
     framing that chases a still-settling arrangement. */
  _advance(now, ms) {
    const tw = this.tween;
    if (tw) {
      const k = ease(Math.min(1, (now - tw.at) / tw.ms));
      for (let a = 0; a < 3; a++) {
        this.target[a] = tw.from.target[a] + (tw.to.target[a] - tw.from.target[a]) * k;
      }
      this.dist = tw.from.dist + (tw.to.dist - tw.from.dist) * k;
      this.camDirty = true;
      if (k >= 1) this.tween = null;
      return true;
    }
    if (this.follow && !this.orbit && !this.panning && !this.pinch) {
      const k = 1 - Math.exp(-Math.min(ms, 60) / FOLLOW_TAU);
      for (let a = 0; a < 3; a++) {
        this.target[a] += (this.follow.target[a] - this.target[a]) * k;
      }
      this.dist += (this.follow.dist - this.dist) * k;
      this.camDirty = true;
      return true;
    }
    return false;
  }

  /* ------------------------------------------------------ screen mapping */

  _project() {
    /* the pending camera FIRST -- it is what decides the version */
    if (this.camDirty) this._camera();
    if (this.scrCamAt === this.camAt && this.scrPosAt === this.posAt) return;
    const { pos, scr, view: v, proj: p, nodes } = this;
    const halfW = this.w / 2, halfH = this.h / 2;
    for (let i = 0; i < nodes.length; i++) {
      const o = i * 3, x = pos[o], y = pos[o + 1], z = pos[o + 2];
      const dist = -(v[2] * x + v[6] * y + v[10] * z + v[14]);
      const s = i * 4;
      if (dist <= this.near) { scr[s + 3] = -1; continue; }
      const ex = v[0] * x + v[4] * y + v[8] * z + v[12];
      const ey = v[1] * x + v[5] * y + v[9] * z + v[13];
      scr[s] = halfW + p[0] * ex / dist * halfW;
      scr[s + 1] = halfH - p[5] * ey / dist * halfH;
      /* the radius the star is DRAWN with, ceiling included */
      scr[s + 2] = starPx(nodes[i].size, dist, this.pxScale);
      scr[s + 3] = dist;
    }
    this.scrCamAt = this.camAt;
    this.scrPosAt = this.posAt;
  }

  /* The memory under a pointer, or null. Nearest to the camera wins where two
     overlap, which is what a reader aiming at the star in front expects. */
  nodeAt(cx, cy) {
    this._project();
    const scr = this.scr;
    let best = null, bestDist = Infinity;
    for (let i = 0; i < this.nodes.length; i++) {
      const s = i * 4, dist = scr[s + 3];
      if (dist < 0 || dist >= bestDist) continue;
      /* the visible edge, not the core radius: STAR_EDGE is where the
         fragment stops drawing the disc */
      const reach = Math.max(7, scr[s + 2] * STAR_EDGE + 4);
      const dx = scr[s] - cx, dy = scr[s + 1] - cy;
      if (dx * dx + dy * dy <= reach * reach) { best = this.nodes[i]; bestDist = dist; }
    }
    return best;
  }

  /* A pointer in the frame's own coordinates -- and the backstop for a
     resize that never arrived: the rect is already being read here, so
     comparing it costs nothing and a stale projection is corrected before it
     answers a click with the wrong memory. */
  _local(e) {
    const r = this.cv.getBoundingClientRect();
    if (Math.abs(r.width - this.w) > 1 || Math.abs(r.height - this.h) > 1) {
      this.resize();
      this._camera();
    }
    return [e.clientX - r.left, e.clientY - r.top];
  }

  /* ------------------------------------------------------- interaction */

  _down(e) {
    try { this.cv.setPointerCapture(e.pointerId); } catch { /* not capturable */ }
    this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (this.pointers.size === 2) {
      this.orbit = this.panning = null;
      this.pinch = this._pinchFrom();
      return;
    }
    if (this.pointers.size > 2) return;
    this.moved = false;
    this.tween = null;
    if (e.button === 1 || e.button === 2) this.panning = { x: e.clientX, y: e.clientY };
    else this.orbit = { x: e.clientX, y: e.clientY, yaw: this.yaw, pitch: this.pitch };
    this.cv.classList.add('grabbing');
  }

  /* The two-finger gesture frozen at the moment it started: every move is
     measured against this rather than the previous frame, so the zoom cannot
     drift and the midpoint stays put under the fingers. */
  _pinchFrom() {
    const [a, b] = [...this.pointers.values()];
    return {
      gap: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)),
      mid: [(a.x + b.x) / 2, (a.y + b.y) / 2],
      dist: this.dist, target: this.target.slice(),
    };
  }

  _move(e) {
    if (this.pointers.has(e.pointerId)) {
      this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    }
    if (this.pinch && this.pointers.size >= 2) {
      const [a, b] = [...this.pointers.values()];
      const gap = Math.max(1, Math.hypot(a.x - b.x, a.y - b.y));
      this.dist = this._clampDist(this.pinch.dist * (this.pinch.gap / gap));
      this.target = this.pinch.target.slice();
      this._camera();
      this._pan((a.x + b.x) / 2 - this.pinch.mid[0], (a.y + b.y) / 2 - this.pinch.mid[1]);
      this.moved = true;
      this.cb.onHover(null);
      return;
    }
    if (this.orbit) {
      const dx = e.clientX - this.orbit.x, dy = e.clientY - this.orbit.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) this.moved = true;
      /* half a turn across the frame, whatever the frame is: the same gesture
         means the same fraction of a rotation on a phone and on a monitor */
      const speed = Math.PI / Math.max(320, this.w);
      if (this.flight) {
        /* looking, not orbiting: the eye holds still and the aim swings, so
           the target moves to wherever the new aim lands at the same range */
        const eye = this.eye.slice();
        this.yaw = this.orbit.yaw - dx * speed;
        this.pitch = this._clampPitch(this.orbit.pitch - dy * speed);
        this._camera();
        this.target = [
          eye[0] + this.fwd[0] * this.dist,
          eye[1] + this.fwd[1] * this.dist,
          eye[2] + this.fwd[2] * this.dist,
        ];
      } else {
        this.yaw = this.orbit.yaw - dx * speed;
        this.pitch = this._clampPitch(this.orbit.pitch + dy * speed);
      }
      this.camDirty = true;
      this.dirty = true;
      this.cb.onHover(null);
      this._wake();
      return;
    }
    if (this.panning) {
      this._pan(e.clientX - this.panning.x, e.clientY - this.panning.y);
      this.panning = { x: e.clientX, y: e.clientY };
      this.moved = true;
      this.cb.onHover(null);
      return;
    }
    const node = this.nodeAt(...this._local(e));
    if (node !== this.hover) {
      const was = this.hover;
      this.hover = node;
      this._touch(was);
      this._touch(node);
    }
    this.cv.style.cursor = this.linkMode ? 'crosshair' : node ? 'pointer' : 'grab';
    /* a finger has no hover: a tip left behind after a tap is a label stuck
       on the canvas with nothing to dismiss it */
    if (e.pointerType === 'touch') { this.cb.onHover(null); return; }
    this.cb.onHover(node, e.clientX, e.clientY);
  }

  _pointerUp(e) {
    if (e) this.pointers.delete(e.pointerId);
    if (this.pointers.size < 2) this.pinch = null;
    /* a second finger lifting off a pinch does not end the gesture */
    if (this.pointers.size) return;
    this.orbit = this.panning = null;
    this.cv.classList.remove('grabbing');
    this.dirty = true;
    this._wake();
  }

  /* A screen drag into a slide of the whole frame, at the scale the camera is
     holding: what is under the pointer moves by that many pixels. */
  _pan(dx, dy) {
    if (this.camDirty) this._camera();
    const k = this.dist / this.pxScale;
    for (let a = 0; a < 3; a++) {
      this.target[a] -= (this.right[a] * dx - this.up[a] * dy) * k;
    }
    this.camDirty = true;
    this.dirty = true;
    this._wake();
  }

  _clampPitch(p) { return Math.max(-PITCH_MAX, Math.min(PITCH_MAX, p)); }

  _clampDist(d) {
    return Math.max(DIST_MIN, Math.min(this.radius * DIST_SPAN + 800, d));
  }

  _wheel(e) {
    e.preventDefault();
    this.tween = null;
    this.follow = null;
    if (this.flight) {
      /* in flight the wheel is a throttle: it moves the whole frame along the
         aim rather than reeling the camera in toward a point */
      const push = this._reach() * (e.deltaY < 0 ? .16 : -.16);
      for (let a = 0; a < 3; a++) this.target[a] += this.fwd[a] * push;
    } else {
      this.dist = this._clampDist(this.dist * (e.deltaY < 0 ? 1 / 1.13 : 1.13));
    }
    this.camDirty = true;
    this.dirty = true;
    this._wake();
  }

  _click(e) {
    if (this.moved) { this.moved = false; return; }
    const node = this.nodeAt(...this._local(e));
    if (this.linkMode && node) {
      if (!this.linkFrom) {
        this.linkFrom = node;
        this._touch(node);
        this.cb.onLink('from', node);
      } else if (node !== this.linkFrom) {
        this.cb.onLink('pair', this.linkFrom, node);
      }
      return;
    }
    this.select(node ? node.uid : null);
  }

  /* Select a memory and travel to it. Passing null clears the selection and
     leaves the camera where it is -- dismissing a card is not a journey. */
  select(uid, { fly = true } = {}) {
    const node = uid ? this.byUid[uid] : null;
    this.selected = node;
    this.cameFrom = null;
    this._focus(node);
    if (node && fly) this.flyTo(node.uid);
    this.cb.onSelect(node);
  }

  /* Everything one relation from `node`, as the set the drawing reads to
     decide what is still at full strength. Rewrites every memory's style,
     which is a click and not a frame. */
  _focus(node) {
    this.focusSet = node
      ? new Set([node.uid, ...this.neighbours(node.uid).map(p => p.uid)])
      : null;
    this._uploadStyle();
  }

  /* Step to the next memory along the selection's relations, and select it.
     This is how a reader walks the store: land on a star, then move from
     neighbour to neighbour without going back to a list.

     `cameFrom` is where the last step arrived from, so pressing the same key
     again carries on outward instead of bouncing between two memories. */
  hop(step) {
    const from = this.selected;
    if (!from) return null;
    const peers = this.neighbours(from.uid);
    if (!peers.length) return null;
    let at = peers.findIndex(p => p.uid === this.cameFrom);
    if (at < 0) at = step > 0 ? -1 : 0;
    const node = peers[((at + step) % peers.length + peers.length) % peers.length];
    this.selected = node;
    this.cameFrom = from.uid;
    this._focus(node);
    this.flyTo(node.uid);
    this.cb.onSelect(node);
    return node;
  }

  /* The memories one relation away, most-connected first. */
  neighbours(uid) {
    const seen = new Set();
    return (this.adj[uid] || [])
      .filter(u => !seen.has(u) && seen.add(u))
      .map(u => this.byUid[u])
      .filter(Boolean)
      .sort((a, b) => (b.degree || 0) - (a.degree || 0));
  }

  /* ------------------------------------------------------------- flight */

  setFlight(on, held = false) {
    if (on === this.flight) return;
    this.flight = on;
    this.flightHeld = on ? held : false;
    if (!on) this.keys.clear();
    /* the frame says which mode this is, and the view owns the frame */
    this.cb.onFlight(on);
    this.dirty = true;
    this._wake();
  }

  _onKeyDown(e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || '')) return;
    /* a dialog over the canvas owns the keyboard: the class is core/ui.js's
       scrim, and a modal's focus is often a button rather than a field */
    if (document.querySelector('.modal-scrim')) return;
    if (e.key === 'Shift' && !e.repeat && !this.flight) { this.setFlight(true, true); return; }
    if (this.flight && /^(Key[WASDQE]|Arrow(Up|Down|Left|Right))$/.test(e.code)) {
      this.keys.add(e.code);
      e.preventDefault();
      this._wake();
      return;
    }
    /* with a memory selected, the arrows walk its relations */
    if (this.selected && (e.code === 'ArrowRight' || e.code === 'ArrowLeft')) {
      if (this.hop(e.code === 'ArrowRight' ? 1 : -1)) e.preventDefault();
      return;
    }
    if (this.selected && e.code === 'Enter') {
      this.cb.onOpen(this.selected);
      e.preventDefault();
      return;
    }
    if (e.code === 'KeyC') { this.fit(); e.preventDefault(); }
  }

  _onKeyUp(e) {
    if (e.key === 'Shift' && this.flightHeld) this.setFlight(false);
    this.keys.delete(e.code);
  }

  /* How fast flight moves and how hard its throttle pushes: the framing
     distance, held to FLY_REACH. */
  _reach() {
    return Math.min(this.dist, FLY_REACH);
  }

  /* One frame of flight. */
  _flyStep(ms) {
    if (!this.flight || !this.keys.size) return false;
    if (this.camDirty) this._camera();
    const v = Math.min(ms, 40) * this._reach() * .0022;
    const k = this.keys;
    const add = (axis, amount) => {
      for (let a = 0; a < 3; a++) this.target[a] += axis[a] * amount;
    };
    if (k.has('KeyW') || k.has('ArrowUp')) add(this.fwd, v);
    if (k.has('KeyS') || k.has('ArrowDown')) add(this.fwd, -v);
    if (k.has('KeyD') || k.has('ArrowRight')) add(this.right, v);
    if (k.has('KeyA') || k.has('ArrowLeft')) add(this.right, -v);
    if (k.has('KeyE')) add(this.up, v);
    if (k.has('KeyQ')) add(this.up, -v);
    this.camDirty = true;
    return true;
  }

  /* ----------------------------------------------------------- spotlight */

  /* Marks which memories are OUT rather than removing any: the arrangement,
     the framing and the legend counts all stay exactly as they were, so what
     the reader learned from the shape before typing is still true after.
     Returns how many matched, and the best one to travel to. */
  spotlight(raw) {
    const terms = String(raw || '').toLowerCase().split(/\s+/).filter(Boolean);
    let count = 0, first = null;
    for (const node of this.nodes) {
      if (!terms.length) { node.miss = false; count++; continue; }
      /* the name AND the opening line of the body, plus every domain it
         belongs to and its tags -- whichever of those a reader would type */
      const hay = `${node.name} ${node.label || ''} ${node.domain || ''} `
        + `${(node.also || []).join(' ')} ${node.tags || ''}`;
      node.miss = !terms.every(w => hay.toLowerCase().includes(w));
      if (!node.miss) {
        count++;
        if (!first || (node.degree || 0) > (first.degree || 0)) first = node;
      }
    }
    this._uploadStyle();
    return { count, first };
  }

  toggleLinkMode() {
    this.linkMode = !this.linkMode;
    const was = this.linkFrom;
    this.linkFrom = null;
    this.cv.classList.toggle('linkmode', this.linkMode);
    this._touch(was);
    return this.linkMode;
  }

  clearLinkFrom() {
    const was = this.linkFrom;
    this.linkFrom = null;
    this._touch(was);
  }

  /* ------------------------------------------------------------- drawing */

  _wake() {
    if (!this.running || this.raf) return;
    this.raf = requestAnimationFrame(this._loop);
  }

  _loop(now) {
    this.raf = 0;
    if (!this.running) return;
    /* Under reduced motion the universe is not shown condensing: there is
       nothing to paint until the arrangement is finished, and the worker's
       next message is what wakes the loop back up. */
    if (!this.motion && !this.settled) return;
    const ms = now - (this.lastFrame || now - 16);
    const moving = this._advance(now, ms);
    const flying = this._flyStep(ms);
    const live = !!(moving || flying || this.orbit || this.panning
                    || this.pinch || !this.settled);
    /* At rest the only thing left is the twinkle, and a slow brightness
       wander looks the same at 30fps as at 60. */
    if (live || this.dirty || (this.motion && now - this.lastFrame >= 1000 / IDLE_FPS)) {
      this.lastFrame = now;
      this.dirty = false;
      this.draw(now);
    }
    if (live || this.motion) this.raf = requestAnimationFrame(this._loop);
  }

  draw(now) {
    const gl = this.gl;
    if (this.camDirty) this._camera();
    gl.clear(gl.COLOR_BUFFER_BIT);

    const twinkle = this.motion ? 1 : 0;
    const time = now * .001 * TWINKLE_HZ;

    gl.useProgram(this.pSky.p);
    gl.bindVertexArray(this.skyVAO);
    gl.uniformMatrix4fv(this.pSky.u.u_sky, false, this.sky);
    gl.uniform1f(this.pSky.u.u_radius, (this.near + this.far) * .35);
    gl.uniform1f(this.pSky.u.u_dpr, this.dpr);
    gl.uniform1f(this.pSky.u.u_time, time);
    gl.uniform1f(this.pSky.u.u_twinkle, twinkle);
    gl.uniform3fv(this.pSky.u.u_color, this.colors.sky);
    gl.drawArrays(gl.POINTS, 0, SKY_STARS);

    if (this.edges.length) {
      gl.useProgram(this.pLink.p);
      gl.bindVertexArray(this.linkVAO);
      gl.uniformMatrix4fv(this.pLink.u.u_view, false, this.view);
      gl.uniformMatrix4fv(this.pLink.u.u_proj, false, this.proj);
      gl.uniform1f(this.pLink.u.u_px, this.pxScale);
      gl.uniform1f(this.pLink.u.u_minPx, MIN_PX);
      gl.uniform1f(this.pLink.u.u_maxPx, MAX_PX);
      gl.uniform4f(this.pLink.u.u_width, LINK_HALF, LINK_HOT, LINK_GAP, LINK_CLEAR);
      gl.uniform2fv(this.pLink.u.u_fog, this.fog);
      gl.uniform3fv(this.pLink.u.u_color, this.colors.edge);
      gl.uniform3fv(this.pLink.u.u_accent, this.colors.accent);
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, this.edges.length);
    }

    if (this.nodes.length) {
      gl.useProgram(this.pStar.p);
      gl.bindVertexArray(this.starVAO);
      gl.uniformMatrix4fv(this.pStar.u.u_view, false, this.view);
      gl.uniformMatrix4fv(this.pStar.u.u_proj, false, this.proj);
      gl.uniform1f(this.pStar.u.u_px, this.pxScale);
      gl.uniform1f(this.pStar.u.u_minPx, MIN_PX);
      gl.uniform1f(this.pStar.u.u_maxPx, MAX_PX);
      gl.uniform2fv(this.pStar.u.u_fog, this.fog);
      gl.uniform1f(this.pStar.u.u_time, time);
      gl.uniform1f(this.pStar.u.u_twinkle, twinkle);
      gl.uniform3fv(this.pStar.u.u_accent, this.colors.accent);
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, this.nodes.length);
    }
    gl.bindVertexArray(null);

    this._drawLabels();
  }

  /* The most-connected memories, as label candidates. Sorted once: WHICH of
     them is named depends on the camera, which ones are ELIGIBLE does not,
     and scoring a hundred thousand names per frame would. */
  _labelPool() {
    this.pool = this.nodes.slice()
      .sort((a, b) => (b.degree || 0) - (a.degree || 0))
      .slice(0, LABEL_POOL);
    /* where a memory stands in that order, so the busiest few can be named
       from a distance where every star is down at the pixel floor -- a wide
       shot of a large store would otherwise carry no names at all */
    this.pool.forEach((node, at) => { node.rank = at; });
  }

  _drawLabels() {
    /* nothing is named while the camera is moving: the names would be
       unreadable, and the projection they need is the frame's whole cost */
    const quiet = !this.orbit && !this.panning && !this.pinch && this.settled;
    if (!quiet) {
      this.lx.clearRect(0, 0, this.w, this.h);
      return;
    }
    /* Redrawn on every painted frame, with no flag deciding whether to skip
       it. Two dozen plates and a clearRect are cheaper than the class of bug
       a skipped one buys: the names live on their own canvas, so a frame this
       pass declines to repaint is a frame of labels sitting over stars that
       have already moved. */
    const lx = this.lx;
    lx.clearRect(0, 0, this.w, this.h);
    this._project();

    /* how many names the frame is allowed at the distance it is holding */
    const budget = Math.max(LABEL_MIN, Math.min(LABEL_MAX,
      Math.round(LABEL_MAX * HOP_DIST * 2 / Math.max(1, this.dist))));
    const cands = [];
    const push = node => {
      /* a memory the spotlight missed or the selection pushed to context is
         not named: what is at full strength is what the reader is reading */
      if (!node || this._fade(node) < 1) return;
      const s = node.i * 4, dist = this.scr[s + 3];
      if (dist < 0) return;
      const x = this.scr[s], y = this.scr[s + 1];
      if (x < -40 || y < LABEL_PAD_Y || x > this.w + 40 || y > this.h - LABEL_PAD_Y) return;
      const lead = node === this.hover || node === this.selected || node === this.linkFrom;
      const hub = node.rank != null && node.rank < budget * 2;
      if (this.scr[s + 2] < LABEL_MIN_PX && !lead && !hub) return;
      cands.push({ node, lead, x, y, r: this.scr[s + 2], score: (1 + (node.degree || 0)) / dist });
    };
    for (const node of this.pool) push(node);
    for (const node of [this.selected, this.linkFrom, this.hover]) {
      if (node && !cands.some(c => c.node === node)) push(node);
    }
    /* what the reader is pointing at or standing on is named first, and the
       rest by how much of the frame they occupy */
    cands.sort((a, b) => (b.lead ? 1 : 0) - (a.lead ? 1 : 0) || b.score - a.score);

    lx.font = `${LABEL_FONT}px ${this.colors.font}`;
    lx.textBaseline = 'middle';
    lx.lineWidth = 1;

    /* The chrome goes in first, as boxes already taken: the toolbar, the
       legend, the card and the banners all float over this canvas and are
       painted above it, so a name placed under one is a name nobody reads. */
    const taken = this.cb.obstacles();
    let drawn = 0;
    for (const c of cands) {
      if (drawn >= budget) break;
      /* A name belongs inside the frame: it is cut to what the canvas can
         hold and then, if it still runs past the right edge, it changes
         sides. The wrap clips its own overflow, so on a 375px frame anything
         wider than this is lost mid-word. */
      const room = this.w - LABEL_EDGE * 2;
      const text = clip(c.node.name, Math.max(10, Math.min(
        c.lead ? LABEL_CLIP_LEAD : LABEL_CLIP, Math.floor(room / LABEL_EM))));
      const width = lx.measureText(text).width;
      const y = c.y;
      const edge = c.r * STAR_EDGE;
      let x = c.x + edge + 8;
      if (x + width > this.w - LABEL_EDGE) {
        x = Math.max(LABEL_EDGE, c.x - edge - 8 - width);
      }
      const box = [x - LABEL_PAD_X, y - LABEL_PAD_Y,
                   width + LABEL_PAD_X * 2, LABEL_PAD_Y * 2];
      if (taken.some(t => box[0] < t[0] + t[2] && t[0] < box[0] + box[2]
                       && box[1] < t[1] + t[3] && t[1] < box[1] + box[3])) continue;
      taken.push(box);
      lx.beginPath();
      if (lx.roundRect) lx.roundRect(box[0], box[1], box[2], box[3], 3);
      else lx.rect(box[0], box[1], box[2], box[3]);
      lx.fillStyle = this.colors.plate;
      lx.fill();
      if (c.lead) {
        lx.strokeStyle = this.colors.lead;
        lx.stroke();
      }
      lx.fillStyle = c.lead ? this.colors.lead : this.colors.ink;
      lx.fillText(text, x, y);
      drawn++;
    }
  }
}

const clip = (text, max) => {
  const s = String(text || '');
  return s.length > max ? `${s.slice(0, max - 1).trimEnd()}…` : s;
};
