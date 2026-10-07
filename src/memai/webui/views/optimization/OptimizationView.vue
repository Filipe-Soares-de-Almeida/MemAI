<script setup lang="ts">
/* Optimization runs, batches of suggested edits awaiting a human decision, at four addresses: the
   month (?month, ?day), a day's open suggestions (?review), one run (?run), one kind of it (&kind). */
import { dayKey } from '../../core/dom.ts';
import * as client from '../../api/client.ts';
import type { OptimizationSummary, RunRow } from '../../api/types.ts';
import type { ViewProps } from '../../core/vue.ts';
import MonthView from './MonthView.vue';
import ReviewView from './ReviewView.vue';
import RunView from './RunView.vue';
import { dayScope, kindScope } from './scope.ts';

const props = defineProps<ViewProps>();

const runId = Number(props.params.get('run') || 0);
const review = props.params.get('review') || '';
const kind = props.params.get('kind') || '';

const runs: RunRow[] = runId ? [] : (await client.optimization.runs()).runs;
const summary: OptimizationSummary | null = runId ? await client.optimization.summary({ run: runId }) : null;
/* which runs a day holds is decided here, against the reader's local day */
const scope = review && !runId ? dayScope(review, runs.filter(r => dayKey(new Date(r.created_at)) === review))
  : summary && kind ? kindScope(summary, kind) : null;
</script>

<template>
  <ReviewView v-if="scope" :scope="scope" />
  <RunView v-else-if="summary" :summary="summary" />
  <MonthView v-else :runs="runs" :params="params" />
</template>
