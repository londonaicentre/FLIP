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
    <AiCard>
        <div class="flex items-center justify-between px-5 py-4">
            <div>
                <div class="text-xs font-semibold tracking-wider uppercase font-mono text-gray-500 dark:text-gray-400">
                    Aggregated cohort {{ anyRunning ? "(live)" : "" }}
                </div>
                <div class="mt-1.5 flex items-baseline gap-2">
                    <span class="text-4xl font-bold tracking-tight tabular-nums text-gray-900 dark:text-gray-100">
                        {{ totalCount.toLocaleString() }}
                    </span>
                    <span class="text-sm text-gray-500 dark:text-gray-400">
                        {{ anyRunning ? "records so far" : "records" }}
                    </span>
                </div>
                <div class="mt-1.5 text-xs text-gray-500 dark:text-gray-400">
                    {{ subtitle }}
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

interface IProps {
    submitting?: boolean;
}

const props = withDefaults(defineProps<IProps>(), { submitting: false });

const projectStore = useProjectStore();
const trustStore = useTrustStore();

const results = ref<IResults | null>(null);

// Same SWRV key as PerTrustResponse — useSWRV dedupes by key so this is a
// shared cache hit, not a second fetch.
const { data } = useSWRV(
    () => projectStore.project?.query?.id ? `/cohort/${projectStore.project.query.id}` : "",
    getOMOPResults,
    {
        errorRetryInterval: 1_000,
        refreshInterval: projectStore.project?.status !== "UNSTAGED" ? 0 : 10_000
    }
);

watch(data, (v) => { results.value = (v as IResults | null | undefined) ?? null; }, { immediate: true });

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

const trustCount = computed(() => {
    const trusts = Array.isArray(trustStore.getTrusts) ? trustStore.getTrusts : [];

    return trusts.length;
});

const completeCount = computed(() => {
    if (props.submitting) return 0;
    const trusts = Array.isArray(trustStore.getTrusts) ? trustStore.getTrusts : [];
    const counts = perTrustCounts.value;

    return trusts.filter(t => t.id in counts).length;
});

const erroredCount = computed(() => {
    if (props.submitting) return 0;
    const trusts = Array.isArray(trustStore.getTrusts) ? trustStore.getTrusts : [];
    const errors = perTrustErrors.value;

    return trusts.filter(t => t.id in errors).length;
});

// Trusts whose cohort is below the privacy threshold and was suppressed (the exact count,
// which may be zero, is hidden). Surfaced in the subtitle so the headline total isn't
// misread as the whole picture (#519).
const suppressedCount = computed(() => {
    if (props.submitting) return 0;
    const trusts = Array.isArray(trustStore.getTrusts) ? trustStore.getTrusts : [];
    const r = results.value as unknown as IResultsShape | null;
    const suppressed = new Set(r?.trustSuppressed ?? []);

    return trusts.filter(t => suppressed.has(t.id)).length;
});

const runningCount = computed(() => Math.max(0, trustCount.value - completeCount.value - erroredCount.value));

const anyRunning = computed(() => runningCount.value > 0);

const totalCount = computed(() => Object.values(perTrustCounts.value).reduce((acc, n) => acc + (n || 0), 0));

const subtitle = computed(() => {
    const parts: string[] = [];
    parts.push(`${completeCount.value} trusts complete`);
    if (runningCount.value > 0) parts.push(`${runningCount.value} running`);
    if (erroredCount.value > 0) parts.push(`${erroredCount.value} errored`);
    if (suppressedCount.value > 0) parts.push(`${suppressedCount.value} suppressed`);

    return parts.join(" · ");
});
</script>
