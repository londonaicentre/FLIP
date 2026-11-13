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
    <div class="overflow-hidden bg-white border border-gray-200 rounded-xl dark:bg-dark-canvas dark:border-dark-border">
        <div class="flex items-center justify-between px-5 py-4">
            <div>
                <div class="text-xs font-semibold text-gray-500 dark:text-gray-300">
                    Aggregated cohort {{ anyRunning ? "(live)" : "" }}
                </div>
                <div class="mt-1.5 flex items-baseline gap-2">
                    <span class="text-4xl font-bold tracking-tight tabular-nums text-gray-900 dark:text-gray-100">
                        {{ totalCount.toLocaleString() }}
                    </span>
                    <span class="text-sm text-gray-500 dark:text-gray-300">
                        {{ anyRunning ? "records so far" : "records" }}
                    </span>
                </div>
                <div class="mt-1.5 text-note text-gray-500 dark:text-gray-300">
                    {{ subtitle }}
                </div>
            </div>
        </div>
    </div>
</template>

<script lang="ts" setup>
import { computed } from "vue";

import { useCohortResults } from "@/composables/useCohortResults";
import { useProjectStore } from "@/store/project";
import { useTrustStore } from "@/store/trusts";

interface IProps {
    submitting?: boolean;
}

const props = withDefaults(defineProps<IProps>(), { submitting: false });

const projectStore = useProjectStore();
const trustStore = useTrustStore();

// Same SWRV key as PerTrustResponse, so this is a shared cache hit, not a second fetch.
const { perTrustCounts, perTrustErrors, perTrustSuppressed } = useCohortResults();

// Restrict the subtitle's counts to the trusts this project actually queried, so the
// segments (complete · running · errored · suppressed) share one denominator and stay
// consistent even if the trust store and the query response ever drift. Falls back to
// "all known trusts" when the query hasn't loaded yet (or pre-#519 data lacks the field),
// preserving prior behaviour (e.g. all trusts shown running before any results arrive).
const queriedTrusts = computed(() => {
    const ids = projectStore.project?.query?.queriedTrustIds;
    const queried = ids?.length ? new Set(ids) : null;
    const trusts = Array.isArray(trustStore.getTrusts) ? trustStore.getTrusts : [];

    return queried ? trusts.filter(t => queried.has(t.id)) : trusts;
});

const trustCount = computed(() => queriedTrusts.value.length);

const completeCount = computed(() =>
    props.submitting ? 0 : queriedTrusts.value.filter(t => t.id in perTrustCounts.value).length);

const erroredCount = computed(() =>
    props.submitting ? 0 : queriedTrusts.value.filter(t => t.id in perTrustErrors.value).length);

// Trusts whose cohort is below the privacy threshold and was suppressed (the exact count,
// which may be zero, is hidden). Surfaced in the subtitle so the headline total isn't
// misread as the whole picture (#519).
const suppressedCount = computed(() =>
    props.submitting ? 0 : queriedTrusts.value.filter(t => perTrustSuppressed.value.has(t.id)).length);

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
