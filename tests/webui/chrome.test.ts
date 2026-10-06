import { describe, expect, it } from 'vitest';
import { createApp, defineComponent, h, nextTick, ref } from 'vue';
import { closeModal, confirmModal, modalOpen, openModal, promptModal, toast,
         typedConfirmModal } from '../../src/memai/webui/core/ui.js';
import Picker from '../../src/memai/webui/components/Picker.vue';
import { catalog } from './support.js';

const en = catalog('en');
const scrims = () => document.querySelectorAll('#modalRoot .modal-scrim');
const type = (input: HTMLInputElement, value: string) => {
  input.value = value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
};

describe('dialogs on the modal stack', () => {
  it('stack, close the innermost first, and hand the caret back to each opener', () => {
    const opener = document.createElement('button');
    document.body.append(opener);
    opener.focus();
    const outer = openModal({ title: 'Outer', bodyHTML: '<button id="inner-opener">open</button>' });
    expect(outer.classList.contains('stacked')).toBe(false);
    (outer.querySelector('#inner-opener') as HTMLButtonElement).focus();
    const inner = openModal({ title: 'Inner', bodyHTML: '<p>second</p>' });
    expect(inner.classList.contains('stacked')).toBe(true);
    expect(scrims()).toHaveLength(2);

    closeModal();
    expect(scrims()).toHaveLength(1);
    expect(document.activeElement?.id).toBe('inner-opener');
    closeModal();
    expect(modalOpen()).toBe(false);
    expect(document.activeElement).toBe(opener);
  });

  it('keep the markup the views wire into', () => {
    const scrim = openModal({ title: '<b>Rename</b>', ariaLabel: 'Rename',
                              bodyHTML: '<input id="nm">', footHTML: '<button data-ok>Go</button>' });
    const dialog = scrim.querySelector('.modal') as HTMLElement;
    expect(dialog.getAttribute('role')).toBe('dialog');
    expect(dialog.getAttribute('aria-label')).toBe('Rename');
    expect(scrim.querySelector('.modal-head')?.innerHTML).toBe('<b>Rename</b>');
    expect(document.activeElement?.id).toBe('nm');
    closeModal();
  });

  it('confirm resolves true on its button, false on cancel and on a close', async () => {
    const yes = confirmModal({ title: 'Archive?', body: 'It can be restored.' });
    (document.querySelector('[data-ok]') as HTMLButtonElement).click();
    expect(await yes).toBe(true);
    const no = confirmModal({ title: 'Archive?', body: '' });
    (document.querySelector('[data-x]') as HTMLButtonElement).click();
    expect(await no).toBe(false);
    const closed = confirmModal({ title: 'Archive?', body: '', danger: true });
    expect(document.querySelector('[data-ok]')?.classList.contains('btn-danger')).toBe(true);
    closeModal();
    await nextTick();
    expect(await closed).toBe(false);
    expect(modalOpen()).toBe(false);
  });

  it('prompt submits its line on Enter and null on cancel', async () => {
    const asked = promptModal({ title: 'Name', label: 'Name', value: 'lantern' });
    const field = document.querySelector('[data-in]') as HTMLInputElement;
    expect(document.activeElement).toBe(field);
    type(field, 'lantern room');
    field.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(await asked).toBe('lantern room');
    const dropped = promptModal({ title: 'Name', label: 'Name' });
    (document.querySelector('[data-x]') as HTMLButtonElement).click();
    expect(await dropped).toBeNull();
  });

  it('typed confirm arms its button only on the exact phrase', async () => {
    const asked = typedConfirmModal({ title: 'Delete', phrase: 'DELETE acme', okLabel: 'Delete' });
    const field = document.querySelector('[data-phrase]') as HTMLInputElement;
    const ok = document.querySelector('[data-ok]') as HTMLButtonElement;
    const state = document.querySelector('[data-state]') as HTMLElement;
    expect(ok.disabled).toBe(true);
    type(field, 'DELETE acm');
    await nextTick();
    expect(ok.disabled).toBe(true);
    expect(state.textContent).toBe(en['dz.mismatch']);
    type(field, 'DELETE acme');
    await nextTick();
    expect(ok.disabled).toBe(false);
    expect(state.classList.contains('armed')).toBe(true);
    ok.click();
    expect(await asked).toBe(true);
  });
});

describe('toasts', () => {
  it('collapse a repeat into a count and hold a fourth until one leaves', async () => {
    toast('Saved', 'ok');
    toast('Saved', 'ok');
    await nextTick();
    const host = document.getElementById('toasts') as HTMLElement;
    expect(host.querySelectorAll('.toast')).toHaveLength(1);
    expect(host.querySelector('.toast-n')?.textContent).toBe('×2');
    toast('Two');
    toast('Three');
    toast('Four');
    await nextTick();
    expect([...host.querySelectorAll('.toast-msg')].map(e => e.textContent)).toEqual(['Saved', 'Two', 'Three']);
  });

  it('announce a failure as an alert that waits to be closed', async () => {
    toast('Not saved', 'bad', { detail: 'HTTP 500' });
    await nextTick();
    const el = document.querySelector('#toasts .toast.bad') as HTMLElement;
    expect(el.getAttribute('role')).toBe('alert');
    expect(el.querySelector('.toast-detail')?.textContent).toBe('HTTP 500');
  });
});

describe('the picker component', () => {
  it('opens on ArrowDown, picks with the keyboard, and reports through v-model', async () => {
    const host = document.createElement('div');
    document.body.append(host);
    const value = ref('b');
    createApp(defineComponent({
      setup: () => () => h(Picker, {
        id: 'pk', modelValue: value.value, ariaLabel: 'Letter',
        items: [{ value: 'a', label: 'Alpha' }, { value: 'b', label: 'Beta' }, { value: 'c', label: 'Gamma' }],
        'onUpdate:modelValue': (v: string) => { value.value = v; },
      }),
    })).mount(host);
    const btn = host.querySelector('#pk') as HTMLButtonElement;
    expect(btn.dataset.v).toBe('b');
    expect(btn.textContent).toBe('Beta');
    btn.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    expect(btn.getAttribute('aria-expanded')).toBe('true');
    const list = document.querySelector('.pick-list') as HTMLElement;
    list.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    list.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await nextTick();
    expect(value.value).toBe('c');
    expect(btn.dataset.v).toBe('c');
    expect(btn.textContent).toBe('Gamma');
    expect(document.querySelector('.pick-pop')).toBeNull();
    expect(document.activeElement).toBe(btn);
  });
});
