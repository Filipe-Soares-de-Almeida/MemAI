/* The dashboard's motion setting: `system` follows the OS, `always` and `never`
   decide for themselves. The result is one attribute on the root element,
   data-motion="full" | "reduce", and every reduced-motion rule in admin.css keys
   off it. The choice is stored per browser; a storage failure reads as `system`. */

export const MOTION_MODES = ['system', 'always', 'never'];
const STORAGE_KEY = 'memai.motion';
const QUERY = '(prefers-reduced-motion: reduce)';

export const readMotion = () => {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    return MOTION_MODES.includes(v) ? v : 'system';
  } catch { return 'system'; }
};

let system = null;
try { system = matchMedia(QUERY); } catch { /* no media queries: the system asks for nothing */ }

let mode = readMotion();

/* 'reduce' or 'full' for the current mode, with the OS answer read now */
const resolved = () =>
  mode === 'never' || (mode !== 'always' && !!system?.matches) ? 'reduce' : 'full';

const apply = () => { document.documentElement.dataset.motion = resolved(); };

export const getMotion = () => mode;

/* what the canvas and the scripts ask before they animate */
export const motionOn = () => document.documentElement.dataset.motion !== 'reduce';

export const setMotion = next => {
  if (!MOTION_MODES.includes(next)) return;
  mode = next;
  try { localStorage.setItem(STORAGE_KEY, mode); } catch { /* the choice lasts until the page closes */ }
  apply();
};

apply();
/* the system's answer can change while the page is open; only `system` follows it */
system?.addEventListener?.('change', apply);
