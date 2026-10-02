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
                :data-status="snapshot.status"
            >
                <span class="font-medium text-gray-900 dark:text-gray-100">{{ snapshot.trustName }}</span>
                <template v-if="snapshot.status === 'frozen'">
                    <span class="text-gray-500 dark:text-gray-300" data-test="cohort-snapshot-count">
                        {{ formatCount(snapshot.rowCount) }} records · {{ formatSnapshotDate(snapshot.snapshotAt) }}
                    </span>
                    <span
                        v-if="hasDrifted(snapshot)"
                        class="rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-900/40 dark:text-amber-300"
                        data-test="cohort-snapshot-drift"
                    >
                        drifted — approved on {{ formatCount(snapshot.approvedRecordCount) }}
                    </span>
                    <span
                        v-if="snapshot.hasAccessions === false"
                        class="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] font-medium text-gray-600 dark:bg-gray-700 dark:text-gray-300"
                        data-test="cohort-snapshot-tabular"
                    >
                        tabular — no imaging
                    </span>
                    <span
                        v-if="snapshot.error"
                        class="text-amber-800 dark:text-amber-300"
                        data-test="cohort-snapshot-recheck"
                    >
                        {{ snapshot.error }}
                    </span>
                </template>
                <template v-else-if="snapshot.status === 'pending'">
                    <span
                        class="rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-900/40 dark:text-amber-300"
                        data-test="cohort-snapshot-pending"
                    >
                        freezing
                    </span>
                    <span class="text-gray-500 dark:text-gray-300">
                        Cohort not frozen yet — training at this trust will be refused until it is
                    </span>
                </template>
                <template v-else>
                    <span
                        class="rounded bg-red-50 px-1.5 py-0.5 text-[11px] font-medium text-red-700 dark:bg-red-500/10 dark:text-red-300"
                        data-test="cohort-snapshot-failed"
                    >
                        not frozen
                    </span>
                    <span class="text-red-700 dark:text-red-300" data-test="cohort-snapshot-failed-text">
                        Cohort not frozen — training at this trust will be refused
                    </span>
                    <span
                        v-if="snapshot.error"
                        class="text-gray-500 dark:text-gray-300"
                        data-test="cohort-snapshot-error"
                    >
                        {{ snapshot.error }}
                    </span>
                </template>
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

// The frozen membership record is never replaced (the served cohort can shrink;
// this shows approval-time facts), but a trust's freeze or re-check can still be pending —
// the refresh picks up its completion without a reload (FLIP#857).
const { data, error } = useSWRV(
    () => {
        if (!props.canLoad) {
            return "";
        }

        return `/projects/${route.params.projectId}/cohort-snapshots`;
    },
    getCohortSnapshots,
    {
        refreshInterval: 30000,
        shouldRetryOnError: false
    }
);

useErrorHandler(error);

const snapshots = computed<ICohortSnapshot[]>(() => (props.canLoad ? data.value ?? [] : []));

const hasDrifted = (snapshot: ICohortSnapshot): boolean =>
    snapshot.approvedRecordCount !== null
    && snapshot.approvedRecordCount !== undefined
    && snapshot.rowCount !== null
    && snapshot.approvedRecordCount !== snapshot.rowCount;

const formatCount = (value: number | null): string => (value === null ? "—" : value.toLocaleString());

const formatSnapshotDate = (value: string | null): string =>
    value === null
        ? "—"
        : new Date(value).toLocaleDateString(undefined, {
            day: "numeric",
            month: "short",
            year: "numeric"
        });
</script>
