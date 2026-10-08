/* Floating chrome opened by a function call: toasts, the hover tip, dialogs and menus, each drawn
   by its component in components/. */

import { createApp } from 'vue';
import { tipHide } from './tip.ts';
import AppModal from '../components/AppModal.vue';
import ConfirmDialog from '../components/ConfirmDialog.vue';
import PromptDialog from '../components/PromptDialog.vue';
import TypedConfirmDialog from '../components/TypedConfirmDialog.vue';
import ActionMenu from '../components/ActionMenu.vue';

export { toast, failed } from './toasts.ts';
export { tipShow, tipHide } from './tip.ts';
export { copyUid } from './copy.ts';
export { closeModal, modalOpen } from './modal-stack.ts';

/* ─── the platform's modifier key, as a keycap shows it */

const MAC = /Mac|iPhone|iPad/.test(navigator.userAgentData?.platform || navigator.platform || '');
export const MOD_KEY = MAC ? '⌘' : 'Ctrl';

/* ─── dialogs: each one a component on the shared modal stack, mounted in a detached host that it
   teleports out of. */

/* A dialog from markup strings. Returns its scrim, for the caller to wire; closeModal() or a click
   on the scrim closes it. */
export function openModal({ title, bodyHTML, footHTML, ariaLabel, wide = false, tall = false }) {
  const app = createApp(AppModal, {
    title, ariaLabel, wide, tall, headHtml: title, bodyHtml: bodyHTML, footHtml: footHTML || '',
    onClose: () => app.unmount(),
  });
  return app.mount(document.createElement('div')).scrim;
}

/* Mounts a dialog component; resolves with what it reports through `done`, and a close reports
   its cancel value. */
export function openDialog(component, props = {}) {
  return new Promise(resolve => {
    let settled = false;
    const app = createApp(component, {
      ...props,
      onDone: value => {
        if (settled) return;
        settled = true;
        app.unmount();
        resolve(value);
      },
    });
    app.mount(document.createElement('div'));
  });
}

export const confirmModal = ({ title, body, okLabel, danger = false }) =>
  openDialog(ConfirmDialog, { title, body, okLabel, danger });

export const promptModal = ({ title, body = '', label, placeholder = '', value = '', okLabel,
                              danger = false }) =>
  openDialog(PromptDialog, { title, body, label, placeholder, value, okLabel, danger });

/* The button stays disabled until the field holds `phrase` exactly. */
export const typedConfirmModal = ({ title, bodyHTML = '', phrase, okLabel }) =>
  openDialog(TypedConfirmDialog, { title, bodyHtml: bodyHTML, phrase, okLabel });

/* ─── menus of actions: items are `{label, run, danger, note}` or `{sep: true}`; openCtxMenu lands
   at a pointer, openDropMenu under a control. */

let menu = null;

export function closeCtxMenu() {
  const open = menu;
  menu = null;
  open?.unmount();
}

export const openCtxMenu = (x, y, items) => openMenu(items, { x, y });

export function openDropMenu(btn, items, { align = 'left' } = {}) {
  return openMenu(items, { btn, align });
}

function openMenu(items, at) {
  closeCtxMenu();
  tipHide();            /* whatever opens now owns the screen */
  const live = items.filter(Boolean);
  if (!live.some(i => !i.sep)) return;
  const app = createApp(ActionMenu, {
    items: live, ...at,
    onClose: () => { if (menu === app) closeCtxMenu(); },
  });
  menu = app;
  app.mount(document.createElement('div'));
}
