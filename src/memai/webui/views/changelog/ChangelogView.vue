<script setup lang="ts">
/* Releases: the versions this installation can name, whether it runs the newest, and the commands
   that update it, printed to copy and never run. */
import { computed, ref } from 'vue';
import { fmtAgo, fmtDay } from '../../core/dom.ts';
import { copyCode, toast, failed } from '../../core/ui.js';
import { icon } from '../../core/icons.js';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { Changelog, ReleaseSection } from '../../api/types.ts';
import type { ViewProps } from '../../core/vue.ts';
import LegacyPicker from '../../components/LegacyPicker.vue';

defineProps<ViewProps>();

const data = ref<Changelog>(await client.changelog());
const state = computed(() => data.value.update);
const checking = ref(false);

const bare = (version: string) => String(version || '').replace(/^v/, '');

/* The presets worth a click; an interval outside them is listed as itself. */
const EVERY_HOURS = [1, 3, 6, 12, 24, 48, 168];

const hoursLabel = (n: number) => n >= 24 && n % 24 === 0
  ? t('rls.every.d', { n: n / 24 })
  : t('rls.every.h', { n });

const everyItems = computed(() => {
  const current = state.value.interval_hours;
  const hours = EVERY_HOURS.includes(current)
    ? EVERY_HOURS
    : [...EVERY_HOURS, current].sort((a, b) => a - b);
  return hours.map(n => ({ value: String(n), label: hoursLabel(n) }));
});

/* "No newer release" and "nobody looked" are different answers, so an off or unrun check says so. */
const checkedText = computed(() => {
  const s = state.value;
  if (!s.enabled) return t('rls.checkOff');
  if (!s.checked_at) return t('rls.checkNever');
  if (Date.now() - new Date(s.checked_at).getTime() < 60000) return t('rls.checkedNow');
  return t('rls.checked', { ago: fmtAgo(s.checked_at) });
});

async function checkNow() {
  checking.value = true;
  try {
    const next = await client.update.check();
    if (next.failed) toast(t('rls.msg.failed'), 'warn');
    else if (next.behind) toast(t('rls.msg.behind', { latest: bare(next.latest) }), 'ok');
    else toast(t('rls.msg.current'), 'ok');
    data.value = await client.changelog();
  } catch (err) {
    failed('err.update', err);
  } finally {
    checking.value = false;
  }
}

async function setEvery(value: string) {
  try {
    await client.update.interval({ hours: Number(value) });
    toast(t('rls.msg.every', { every: hoursLabel(Number(value)) }), 'ok');
  } catch (err) { failed('err.update', err); }
}

/* release-please heads the breaking group with a faint text-presentation ⚠, so it gets the emoji. */
const BREAKING = /breaking/i;
const WARN_GLYPH = /^\s*⚠️?\s*/;
const breaking = (group: ReleaseSection) => BREAKING.test(group.title);
const groupTitle = (group: ReleaseSection) =>
  breaking(group) ? group.title.replace(WARN_GLYPH, '') : group.title;
</script>

<!-- v-html carries only the SVG strings core/icons.js builds. -->
<template>
  <div class="anim rl">
    <header class="rl-head">
      <h2 class="rl-title">{{ t('rls.title') }}</h2>
      <div v-if="!state.behind" class="rl-versions is-current">
        <div class="rl-v">
          <span class="rl-v-num">{{ bare(state.current) }}</span>
          <span class="rl-v-label">{{ t('rls.installed') }}</span>
        </div>
        <p class="rl-checked">{{ t('rls.upToDate') }} · {{ checkedText }}</p>
      </div>
      <div v-else class="rl-versions">
        <div class="rl-v">
          <span class="rl-v-num">{{ bare(state.current) }}</span>
          <span class="rl-v-label">{{ t('rls.installed') }}</span>
        </div>
        <span class="rl-v-step" v-html="icon('chevron-right')"></span>
        <div class="rl-v is-latest">
          <span class="rl-v-num">{{ bare(state.latest) }}</span>
          <span class="rl-v-label">{{ t('rls.latest') }}</span>
        </div>
        <p class="rl-checked">{{ t('rls.since', { n: state.behind }) }} · {{ checkedText }}</p>
      </div>
      <div class="rl-check">
        <button type="button" class="btn btn-sm" id="rlCheck" :disabled="!state.enabled || checking"
                :title="state.enabled ? undefined : t('rls.checkOff')"
                @click="checkNow">{{ checking ? t('rls.checking') : t('rls.checkNow') }}</button>
        <label class="inline-label">{{ t('rls.every') }}
          <LegacyPicker id="rlEvery" :value="String(state.interval_hours)" :items="everyItems"
                        :aria-label="t('rls.every')" @pick="setEvery" /></label>
      </div>
    </header>

    <section v-if="state.behind" class="rl-update">
      <h3 class="rl-update-title">{{ t('rls.updateTitle', { v: bare(state.latest) }) }}</h3>
      <p class="rl-update-lead">{{ state.commands.length ? t('rls.updateLead') : t('rls.noCommands') }}</p>
      <ol v-if="state.commands.length" class="rl-steps">
        <li class="rl-step"><p class="rl-step-text">{{ t('rls.stepClose') }}</p></li>
        <li v-for="command in state.commands" :key="command" class="rl-step is-cmd">
          <code>{{ command }}</code>
          <button type="button" class="icon-btn" :data-copy="command" :title="t('rls.copy')"
                  :aria-label="t('rls.copy')" @click="copyCode(command)" v-html="icon('copy')"></button>
        </li>
        <li class="rl-step"><p class="rl-step-text">{{ t('rls.stepReopen') }}</p></li>
      </ol>
      <div class="rl-update-foot">
        <a class="btn btn-sm" :href="state.url" target="_blank"
           rel="noopener noreferrer">{{ t('rls.page') }}</a>
      </div>
    </section>

    <div v-if="data.releases.length" class="rl-stream">
      <article v-for="release in data.releases" :key="release.version" class="rl-item"
               :class="{ 'is-here': release.state === 'installed' }">
        <div class="rl-mark">
          <span class="rl-ver">{{ release.version }}</span>
          <time v-if="release.date" class="rl-date" :datetime="release.date">{{ fmtDay(release.date) }}</time>
          <span v-if="release.state === 'installed'" class="rl-tag is-here">{{ t('rls.state.installed') }}</span>
          <span v-else-if="release.state === 'ahead'" class="rl-tag is-ahead">{{ t('rls.state.ahead') }}</span>
        </div>
        <div class="rl-body">
          <template v-if="release.sections.length">
            <section v-for="(group, g) in release.sections" :key="g" class="rl-group"
                     :class="{ 'is-breaking': breaking(group) }">
              <h3 v-if="groupTitle(group)" class="rl-group-title"><span v-if="breaking(group)" class="rl-warn"
                  aria-hidden="true">⚠️</span>{{ groupTitle(group) }}</h3>
              <ul class="rl-list"><li v-for="(entry, i) in group.entries" :key="i">{{ entry }}</li></ul>
            </section>
          </template>
          <p v-else class="rl-quiet">{{ t('rls.noEntries') }}</p>
          <a v-if="release.url" class="rl-src" :href="release.url" target="_blank"
             rel="noopener noreferrer">{{ t('rls.source') }}</a>
        </div>
      </article>
    </div>
    <div v-else class="empty rl-empty">
      <p>{{ t('rls.noHistory') }}</p>
      <p><a :href="state.url" target="_blank" rel="noopener noreferrer" class="rl-link">{{ t('rls.page') }}</a></p>
    </div>
  </div>
</template>
