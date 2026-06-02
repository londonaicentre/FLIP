<!--
    Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
-->

<template>
    <AiCard class="flex flex-col h-full">
        <div class="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
            <span class="text-xs font-semibold tracking-wider uppercase font-mono text-gray-500 dark:text-gray-400">
                Per-trust response
            </span>
            <span v-if="anyRunning" class="text-xs text-gray-500 dark:text-gray-400">
                {{ completedCount }} / {{ rows.length }} returned
            </span>
        </div>
        <div class="flex-1 overflow-y-auto divide-y divide-gray-200 dark:divide-gray-700">
            <div
                v-if="!rows.length"
                class="px-4 py-6 text-sm text-center text-gray-500 dark:text-gray-400"
            >
                {{ emptyMessage }}
            </div>
            <div
                v-for="row in rows"
                :key="row.id"
                class="grid items-center grid-cols-[auto_1fr_auto] gap-3 px-4 py-3"
            >
                <div class="flex items-center gap-2">
                    <span
                        class="inline-block w-2 h-2 rounded-full"
                        :class="row.errored
                            ? 'bg-red-600'
                            : row.cancelled
                                ? 'bg-gray-300 dark:bg-gray-600'
                                : row.pending
                                    ? 'bg-gray-400'
                                    : row.running
                                        ? 'bg-[#e51170] cq-pulse'
                                        : 'bg-emerald-500'"
                    />
                    <span class="font-mono text-xs font-semibold text-gray-700 dark:text-gray-200">
                        {{ row.code }}
                    </span>
                </div>
                <div class="min-w-0">
                    <div class="text-sm font-semibold truncate text-gray-900 dark:text-gray-100" :title="row.name">
                        {{ row.name }}
                    </div>
                    <div
                        v-if="row.errored && row.error"
                        class="text-xs truncate text-red-600 dark:text-red-400"
                        :title="row.error"
                    >
                        {{ row.error }}
                    </div>
                </div>
                <div class="text-right">
                    <span
                        v-if="row.errored"
                        class="inline-flex items-center gap-1.5 px-2 py-0.5 text-[11px] font-semibold tracking-wide text-white rounded-full bg-red-600"
                        :title="row.error || 'Query failed on this trust'"
                    >
                        <span class="inline-block w-1.5 h-1.5 bg-white rounded-full" />
                        error
                    </span>
                    <span
                        v-else-if="row.cancelled"
                        class="inline-flex items-center gap-1.5 px-2 py-0.5 text-[11px] font-semibold tracking-wide text-gray-500 dark:text-gray-400 rounded-full bg-gray-100 dark:bg-gray-800"
                        title="Project was approved without this trust; query task cancelled"
                    >
                        <span class="inline-block w-1.5 h-1.5 bg-gray-400 rounded-full" />
                        skipped
                    </span>
                    <span
                        v-else-if="row.pending"
                        class="inline-flex items-center gap-1.5 px-2 py-0.5 text-[11px] font-semibold tracking-wide text-gray-700 dark:text-gray-200 rounded-full bg-gray-200 dark:bg-gray-700"
                        title="Queued at the hub; trust hasn't polled for it yet"
                    >
                        <span class="inline-block w-1.5 h-1.5 bg-gray-500 rounded-full" />
                        queued
                    </span>
                    <span
                        v-else-if="row.running"
                        class="inline-flex items-center gap-1.5 px-2 py-0.5 text-[11px] font-semibold tracking-wide text-white rounded-full"
                        style="background-color: #e51170"
                    >
                        <span class="inline-block w-1.5 h-1.5 bg-white rounded-full cq-pulse" />
                        running
                    </span>
                    <span
                        v-else-if="row.suppressed"
                        class="inline-flex items-center gap-1.5 px-2 py-0.5 text-[11px] font-semibold tracking-wide text-amber-700 dark:text-amber-300 rounded-full bg-amber-100 dark:bg-amber-900/40"
                        title="Matched some patients, but the count is below the privacy threshold and was suppressed by the trust"
                    >
                        <span class="inline-block w-1.5 h-1.5 bg-amber-500 rounded-full" />
                        suppressed
                    </span>
                    <span
                        v-else
                        class="text-lg font-semibold tabular-nums text-gray-900 dark:text-gray-100"
                    >
                        {{ row.count.toLocaleString() }}
                    </span>
                </div>
            </div>
        </div>
    </AiCard>
</template>

<script lang="ts" setup>
import useSWRV from "swrv";
import { computed, ref, watch } from "vue";

import AiCard from "@/components/AiCard/AiCard.vue";
import { getOMOPResults, IResults } from "@/services/cohort-query-service";
import { useProjectStore } from "@/store/project";
import { useTrustStore } from "@/store/trusts";
import { filterByQueriedTrustIds } from "@/utils/cohort/query";

interface IProps {
    submitting?: boolean;
}

const props = withDefaults(defineProps<IProps>(), { submitting: false });

const projectStore = useProjectStore();
const trustStore = useTrustStore();

const results = ref<IResults | null>(null);

const { data } = useSWRV(
    () => projectStore.project?.query?.id ? `/cohort/${projectStore.project.query.id}` : "",
    getOMOPResults,
    {
        errorRetryInterval: 1_000,
        refreshInterval: projectStore.project?.status !== "UNSTAGED" ? 0 : 10_000
    }
);

watch(data, (v) => { results.value = (v as IResults | null | undefined) ?? null; }, { immediate: true });

// Per-trust state comes from `trustRecordCounts` and `trustErrors` on the
// response — both populated by the hub aggregator. A trust present in
// trustRecordCounts has responded successfully (count may be 0 for
// privacy-suppressed); a trust in trustErrors failed; a trust in neither is
// still running. We fall back to per-field aggregation only when neither map
// is present (back-compat with older QueryStats rows that pre-date this fix).
interface IFieldShape {
    results: { trustId: string; data: { count: number }[] }[];
}

interface IResultsShape {
    trustsResults?: IFieldShape[];
    trustRecordCounts?: Record<string, number>;
    trustErrors?: Record<string, string>;
    trustSuppressed?: string[];
}

const perTrustCounts = computed<Record<string, number>>(() => {
    const r = results.value as unknown as IResultsShape | null;
    if (r?.trustRecordCounts) return r.trustRecordCounts;
    // Back-compat fallback for QueryStats rows written before the trust_record_counts field existed.
    const trustsResults = r?.trustsResults;
    if (!trustsResults?.length) return {};
    const firstField = trustsResults.find(f => f.results?.length);
    if (!firstField) return {};
    const counts: Record<string, number> = {};
    for (const t of firstField.results) {
        counts[t.trustId] = t.data.reduce((acc, d) => acc + (d.count || 0), 0);
    }

    return counts;
});

const perTrustErrors = computed<Record<string, string>>(() => {
    const r = results.value as unknown as IResultsShape | null;

    return r?.trustErrors ?? {};
});

// Set of trust ids whose count was privacy-suppressed (below threshold). These
// trusts responded successfully but render a "suppressed" chip instead of a
// literal 0 that would read as "no data available". See issue #519.
const perTrustSuppressed = computed<Set<string>>(() => {
    const r = results.value as unknown as IResultsShape | null;

    return new Set(r?.trustSuppressed ?? []);
});

interface IRow {
    id: string;
    code: string;
    name: string;
    count: number;
    pending: boolean;
    cancelled: boolean;
    running: boolean;
    errored: boolean;
    error: string | null;
    suppressed: boolean;
}

const rows = computed<IRow[]>(() => {
    const trusts = Array.isArray(trustStore.getTrusts) ? trustStore.getTrusts : [];
    const counts = perTrustCounts.value;
    const errors = perTrustErrors.value;
    const suppressed = perTrustSuppressed.value;

    // Three cases:
    // 1. Mid-submit: fan-out targets the live trust list — render every
    //    trust as "running" so the user sees the fan-out animate.
    // 2. No query exists yet: nothing to show. Without this branch the
    //    panel would render every current trust as "running" forever,
    //    because the filter helper's undefined-passthrough returns them
    //    all and none have counts/errors.
    // 3. Query exists: filter to the trusts the query was dispatched to.
    //    Trusts that never responded stay visible as "running"; errored
    //    trusts get a red chip; later-joining trusts are excluded.
    let visibleTrusts: typeof trusts;
    if (props.submitting) {
        visibleTrusts = trusts;
    } else if (!projectStore.project?.query) {
        visibleTrusts = [];
    } else {
        visibleTrusts = filterByQueriedTrustIds(trusts, projectStore.project.query.queriedTrustIds);
    }

    // erroredTrustIds is sourced from QueryResult.data on the project, so it
    // catches errored trusts even when the QueryStats aggregator's
    // trustErrors map is empty (timing window after the error post and
    // before re-aggregation lands). pendingTrustIds and cancelledTrustIds
    // come from TrustTask state (PENDING / CANCELLED respectively).
    const erroredSet = new Set(projectStore.project?.query?.erroredTrustIds ?? []);
    const pendingSet = new Set(projectStore.project?.query?.pendingTrustIds ?? []);
    const cancelledSet = new Set(projectStore.project?.query?.cancelledTrustIds ?? []);

    return visibleTrusts
        .map((t) => {
            const errored = !props.submitting && (t.id in errors || erroredSet.has(t.id));
            const responded = !props.submitting && t.id in counts;
            // Precedence: errored > responded > cancelled > queued > running.
            // Mid-submit we treat every trust as "queued" — the hub is about
            // to queue the TrustTask, so showing the pink "running" pulse
            // before the task even exists would be misleading. Once results
            // start landing this naturally transitions to running/responded.
            const cancelled = !props.submitting && !responded && !errored && cancelledSet.has(t.id);
            const pending = !errored && !responded && !cancelled && (
                props.submitting || pendingSet.has(t.id)
            );
            const running = !errored && !responded && !cancelled && !pending;

            return {
                id: t.id,
                code: t.code || t.name,
                name: t.name,
                count: counts[t.id] ?? 0,
                pending,
                cancelled,
                running,
                errored,
                error: errored ? (errors[t.id] || null) : null,
                // Only meaningful once the trust has responded (not errored/in-flight).
                suppressed: responded && suppressed.has(t.id)
            };
        })
        .sort((a, b) => a.code.localeCompare(b.code));
});

// "Returned" = the trust has a result (success or error) or was skipped.
// Pending + running are in-flight states.
const anyRunning = computed(() => rows.value.some(r => r.pending || r.running));
const completedCount = computed(() =>
    rows.value.filter(r => !r.pending && !r.running).length
);
const emptyMessage = computed(() => projectStore.project?.query
    ? "No participating trusts."
    : "Submit a query to see per-trust responses."
);
</script>

<style scoped>
@keyframes cq-pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.4; transform: scale(0.85); }
}
.cq-pulse {
    animation: cq-pulse 1.4s ease-in-out infinite;
}
</style>
