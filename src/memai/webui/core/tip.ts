/* The hover tip in #tip: positioned in an animation frame and measured only when its content
   changes, since both canvases call it on every pointermove. */

let tipHtml = '';
let tipBox: { w: number; h: number } | null = null;
let tipFrame = 0;
const tipAt = { x: 0, y: 0 };

const host = (): HTMLElement | null => document.getElementById('tip');

export function tipShow(html: string, x: number, y: number): void {
  const tip = host();
  if (!tip) return;
  tipAt.x = x; tipAt.y = y;
  if (html !== tipHtml) {
    tip.innerHTML = html;
    tipHtml = html;
    tipBox = null;
  }
  tip.hidden = false;
  if (tipFrame) return;
  tipFrame = requestAnimationFrame(() => {
    tipFrame = 0;
    if (tip.hidden) return;
    if (!tipBox) {
      const r = tip.getBoundingClientRect();
      tipBox = { w: r.width, h: r.height };
    }
    /* clamped both ends: a wrapped tip can be tall enough to leave the top of the window */
    tip.style.left = `${Math.max(10, Math.min(tipAt.x + 14, innerWidth - tipBox.w - 10))}px`;
    tip.style.top = `${Math.max(10, Math.min(tipAt.y + 14, innerHeight - tipBox.h - 10))}px`;
  });
}

export function tipHide(): void {
  const tip = host();
  if (tip) tip.hidden = true;
  tipHtml = '';
  tipBox = null;
}
