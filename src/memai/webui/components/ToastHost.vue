<script setup lang="ts">
/* The toast stack, mounted once into #toasts. It publishes its height as --toast-h so the view
   reserves that much bottom padding and a toast lands on padding rather than over content. */
import { nextTick, onBeforeUnmount, onMounted, watch } from 'vue';
import { armToast, dropToast, holdToast, toasts } from '../core/toasts.ts';
import type { Toast } from '../core/toasts.ts';
import { icon } from '../core/icons.js';
import { t } from '../i18n.ts';

const MARK: Record<string, string> = { ok: 'confirmed', warn: 'unverified', bad: 'contradicted' };

let host: HTMLElement | null = null;
let size: ResizeObserver | null = null;

const publish = () => {
  const h = host && host.children.length ? host.offsetHeight + 10 : 0;
  document.documentElement.style.setProperty('--toast-h', `${h}px`);
};

function act(item: Toast) {
  dropToast(item);
  item.action?.run?.();
}

onMounted(() => {
  host = document.getElementById('toasts');
  /* a message that wraps changes the height without changing the count */
  if (host && typeof ResizeObserver === 'function') {
    size = new ResizeObserver(publish);
    size.observe(host);
  }
});
watch(() => toasts.length, () => nextTick(publish));
onBeforeUnmount(() => size?.disconnect());
</script>

<!-- v-html carries only the SVG strings core/icons.js builds; a failure takes role="alert". -->
<template>
  <div v-for="item in toasts" :key="item.id" class="toast" :class="[item.kind, { out: item.going }]"
       :role="item.kind === 'bad' ? 'alert' : 'status'"
       @pointerenter="holdToast(item)" @focusin="holdToast(item)"
       @pointerleave="armToast(item)" @focusout="armToast(item)"
       @keydown.esc.stop="dropToast(item)">
    <span v-if="MARK[item.kind]" class="toast-mark" v-html="icon(MARK[item.kind])"></span>
    <div class="toast-text">
      <div class="toast-line"><span class="toast-msg">{{ item.msg }}</span><span class="toast-n"
           :hidden="item.count < 2">×{{ item.count }}</span></div>
      <span v-if="item.detail" class="toast-detail">{{ item.detail }}</span>
    </div>
    <button v-if="item.action" type="button" class="btn btn-sm btn-ghost" data-act
            @click="act(item)">{{ item.action.label }}</button>
    <button type="button" class="icon-btn" data-close :aria-label="t('common.close')"
            @click="dropToast(item)" v-html="icon('close')"></button>
  </div>
</template>
