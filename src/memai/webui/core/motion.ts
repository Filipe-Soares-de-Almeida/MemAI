/* The motion setting (`system`, `always`, `never`) as data-motion on the root, which admin.css
   keys reduced motion off; stored per browser, and a storage failure reads as `system`. */

export const MOTION_MODES = ['system', 'always', 'never'] as const;
export type MotionMode = typeof MOTION_MODES[number];

const STORAGE_KEY = 'memai.motion';
const QUERY = '(prefers-reduced-motion: reduce)';

const isMode = (v: unknown): v is MotionMode => (MOTION_MODES as readonly unknown[]).includes(v);

export const readMotion = (): MotionMode => {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    return isMode(v) ? v : 'system';
  } catch { return 'system'; }
};

let system: MediaQueryList | null = null;
try { system = matchMedia(QUERY); } catch { /* no media queries: the system asks for nothing */ }

let mode = readMotion();

/* 'reduce' or 'full' for the current mode, with the OS answer read now */
const resolved = (): 'reduce' | 'full' =>
  mode === 'never' || (mode !== 'always' && !!system?.matches) ? 'reduce' : 'full';

const apply = (): void => { document.documentElement.dataset.motion = resolved(); };

export const getMotion = (): MotionMode => mode;

/* what the canvas and the scripts ask before they animate */
export const motionOn = (): boolean => document.documentElement.dataset.motion !== 'reduce';

export const setMotion = (next: string): void => {
  if (!isMode(next)) return;
  mode = next;
  try { localStorage.setItem(STORAGE_KEY, mode); } catch { /* the choice lasts until the page closes */ }
  apply();
};

apply();
/* the system's answer can change while the page is open; only `system` follows it */
system?.addEventListener?.('change', apply);
