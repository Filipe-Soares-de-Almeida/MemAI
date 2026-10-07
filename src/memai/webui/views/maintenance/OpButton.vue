<script setup lang="ts">
/* A store operation's button: asks first when the operation destroys or rewrites, keeps its label
   beside the spinner while it runs, and has the view read the store again once it lands. */
import { ref } from 'vue';
import { confirmModal, failed, toast } from '../../core/ui.js';
import { t } from '../../i18n.ts';
import { OPS, useMaintenance } from './maintenance.ts';
import type { Op, OpKey } from './maintenance.ts';

const props = withDefaults(defineProps<{ op: OpKey; label: string; cls?: string; disabled?: boolean }>(),
                           { cls: 'btn btn-sm', disabled: false });

const ctx = useMaintenance();
const running = ref(false);

async function run() {
  const op: Op = OPS[props.op];
  if (op.confirm && !(await confirmModal({ title: t('mn.confirm.title'), body: op.confirm,
                                          okLabel: t('common.run'), danger: !!op.danger }))) return;
  running.value = true;
  try {
    toast(op.msg(await op.call() as never), 'ok');
    ctx.touch();
  } catch (err) { failed('err.maintenance', err); }
  running.value = false;
}
</script>

<template>
  <button :class="cls" :data-op="op" :disabled="disabled || running" :aria-busy="running ? 'true' : undefined"
          @click="run"><span v-if="running" class="spin"></span>{{ label }}</button>
</template>
