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
    <div
        v-if="snapshots.length"
        class="px-4 pb-3"
        data-test="cohort-snapshot-summary"
    >
        <p class="font-mono text-[10px] uppercase tracking-wide leading-tight text-gray-500 dark:text-gray-300">
            Approved cohort (frozen at approval)
        </p>
        <ul class="mt-1.5 space-y-1">
            <li
                v-for="snapshot in snapshots"
                :key="snapshot.trustId"
                class="flex flex-wrap items-baseline gap-x-2 text-[13px] leading-snug"
                data-test="cohort-snapshot-row"
            >
                <span class="font-medium text-gray-900 dark:text-gray-100">{{ snapshot.trustName }}</span>
                <span class="text-gray-500 dark:text-gray-300" data-test="cohort-snapshot-count">
                    {{ snapshot.rowCount.toLocaleString() }} records · {{ formatSnapshotDate(snapshot.snapshotAt) }}
                </span>
                <span
                    v-if="hasDrifted(snapshot)"
                    class="rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-900/40 dark:text-amber-300"
                    data-test="cohort-snapshot-drift"
                >
                    drifted — approved on {{ snapshot.approvedRecordCount?.toLocaleString() }}
                </span>
                <span
                    v-if="!snapshot.hasAccessions"
                    class="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] font-medium text-gray-600 dark:bg-gray-700 dark:text-gray-300"
                    data-test="cohort-snapshot-tabular"
                >
                    tabular — no imaging
                </span>
            </li>
        </ul>
    </div>
</template>

<script setup lang="ts">
import useSWRV from "swrv";
import { computed } from "vue";
import { useRoute } from "vue-router";

import useErrorHandler from "@/composables/useErrorHandler";
import { getCohortSnapshots, ICohortSnapshot } from "@/services/project-service";

interface ICohortSnapshotSummaryProps {
    canLoad: boolean;
}

const props = defineProps<ICohortSnapshotSummaryProps>();
const route = useRoute();

// No refreshInterval: the frozen cohort changes only at re-approval, not while the
// page is open — one fetch per project view is the honest cadence (FLIP#857).
const { data, error } = useSWRV(
    () => {
        if (!props.canLoad) {
            return "";
        }

        return `/projects/${route.params.projectId}/cohort-snapshots`;
    },
    getCohortSnapshots,
    { shouldRetryOnError: false }
);

useErrorHandler(error);

const snapshots = computed<ICohortSnapshot[]>(() => (props.canLoad ? data.value ?? [] : []));

const hasDrifted = (snapshot: ICohortSnapshot): boolean =>
    snapshot.approvedRecordCount !== null
    && snapshot.approvedRecordCount !== undefined
    && snapshot.approvedRecordCount !== snapshot.rowCount;

const formatSnapshotDate = (value: string): string =>
    new Date(value).toLocaleDateString(undefined, {
        day: "numeric",
        month: "short",
        year: "numeric"
    });
</script>
