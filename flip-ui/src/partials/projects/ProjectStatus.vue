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
        <div class="p-4">
            <div class="flex flex-row items-center">
                <div class="grow">
                    <h2 class="text-lg font-semibold leading-loose font-heading grow">
                        Imaging project status
                    </h2>
                    <p
                        v-if="canLoad"
                        class="mt-1 text-[13px] leading-snug text-gray-500 dark:text-gray-400"
                    >
                        When this project was approved, an XNAT project was created at each trust.
                        Live import counts below.
                    </p>
                </div>
            </div>
        </div>
        <div v-if="canLoad" class="flex-grow text-sm" data-test="project-status-container">
            <Transition name="fade" mode="out-in">
                <div v-if="!data" class="p-4 space-y-2 transition">
                    <AiSkeleton class="w-1/4 h-8" />
                    <AiSkeleton class="w-full h-8 mt-2" />
                    <AiSkeleton class="w-full h-8" />
                    <AiSkeleton class="w-full h-8" />
                </div>

                <div v-else class="space-y-4">
                    <div class="grid grid-cols-5 overflow-hidden border-t border-b border-gray-200 divide-x divide-gray-200 dark:border-gray-700 dark:divide-gray-700">
                        <div class="min-w-0 p-2.5">
                            <p class="font-mono text-[10px] uppercase tracking-wide leading-tight text-gray-500 dark:text-gray-400 break-words">
                                Trusts onboarded
                            </p>
                            <p class="mt-1.5 text-xl font-semibold leading-none font-heading text-gray-900 dark:text-gray-100" data-test="overview-project-creation">
                                {{ overview.projectCreationCompleted }}/{{ overview.projectCreationTotal }}
                            </p>
                            <p class="mt-1.5 text-[11px] leading-tight text-gray-500 dark:text-gray-400">
                                imaging projects created
                            </p>
                        </div>
                        <div class="min-w-0 p-2.5">
                            <p class="font-mono text-[10px] uppercase tracking-wide leading-tight text-gray-500 dark:text-gray-400 break-words">
                                Studies retrieved
                            </p>
                            <p class="mt-1.5 text-xl font-semibold leading-none font-heading text-gray-900 dark:text-gray-100" data-test="overview-image-retrieval">
                                {{ formatCount(overview.studyRetrievalTotal) }}
                            </p>
                            <p class="mt-1.5 text-[11px] leading-tight text-gray-500 dark:text-gray-400">
                                {{ overviewRetrievalPercent }}% of expected {{ expectedCohortLabel }}
                            </p>
                        </div>
                        <div class="min-w-0 p-2.5">
                            <p class="font-mono text-[10px] uppercase tracking-wide leading-tight text-gray-500 dark:text-gray-400 break-words">
                                Processing
                            </p>
                            <p class="mt-1.5 text-xl font-semibold leading-none font-heading text-gray-900 dark:text-gray-100">
                                {{ formatCount(overview.processingTotal) }}
                            </p>
                            <p class="mt-1.5 text-[11px] leading-tight text-gray-500 dark:text-gray-400">
                                in flight at trusts
                            </p>
                        </div>
                        <div class="min-w-0 p-2.5">
                            <p class="font-mono text-[10px] uppercase tracking-wide leading-tight text-gray-500 dark:text-gray-400 break-words">
                                Queued
                            </p>
                            <p class="mt-1.5 text-xl font-semibold leading-none font-heading text-gray-900 dark:text-gray-100">
                                {{ formatCount(overview.queuedTotal) }}
                            </p>
                            <p class="mt-1.5 text-[11px] leading-tight text-gray-500 dark:text-gray-400">
                                waiting on import workers
                            </p>
                        </div>
                        <div class="min-w-0 p-2.5">
                            <p class="font-mono text-[10px] uppercase tracking-wide leading-tight text-gray-500 dark:text-gray-400 break-words">
                                Failed
                            </p>
                            <p
                                class="mt-1.5 text-xl font-semibold leading-none font-heading"
                                :class="overview.failedTotal > 0
                                    ? 'text-red-600 dark:text-red-400'
                                    : 'text-gray-900 dark:text-gray-100'"
                            >
                                {{ formatCount(overview.failedTotal) }}
                            </p>
                            <p class="mt-1.5 text-[11px] leading-tight text-gray-500 dark:text-gray-400">
                                {{ overview.failedTotal > 0 ? "requires attention" : "no errors" }}
                            </p>
                        </div>
                    </div>

                    <div class="w-full border-t border-gray-200 dark:border-gray-700">
                        <div class="w-full">
                            <ul role="list" class="grid grid-cols-1 gap-3 p-3 xl:grid-cols-2">
                                <li
                                    v-for="project in sortedData"
                                    :key="project.trustId"
                                    class="relative p-4 border border-gray-200 rounded-lg dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800"
                                >
                                    <div class="flex flex-col gap-3">
                                        <!-- Top row: trust name + reimport count -->
                                        <div class="flex items-center justify-between gap-3">
                                            <h2 class="font-bold font-heading text-base text-gray-900 dark:text-gray-100" :data-test="`trust-name-${project.trustId}`">
                                                {{ project.trustName }}
                                            </h2>
                                            <div
                                                v-if="project.reimportCount !== undefined && project.reimportCount !== null && project.projectCreationCompleted && maxReimportCount > 0"
                                                v-tippy="{ content: project.reimportCount >= maxReimportCount ? ReimportCountLimitMessage : 'Reimport attempts', placement: 'top' }"
                                                class="flex items-center gap-1.5 shrink-0"
                                            >
                                                <RefreshIcon
                                                    v-if="project.reimportCount < maxReimportCount"
                                                    class="w-4 h-4 text-green-500"
                                                />
                                                <ExclamationCircleIcon
                                                    v-else
                                                    class="w-4 h-4 text-yellow-500"
                                                />
                                                <span
                                                    class="font-mono text-xs text-gray-500 dark:text-gray-400"
                                                    :data-test="`project-reimport-status-${project.trustId}`"
                                                >
                                                    {{ project.reimportCount }} / {{ maxReimportCount }}
                                                </span>
                                            </div>
                                        </div>
                                        <!-- Stacked import bar — design ref:
                                             06_imaging_status/imaging-status.jsx
                                             ImagingStatusA / IPSImportBar.
                                             Always rendered (even when importStatus is absent)
                                             so every card has the same body height and the
                                             footer pin lands at the card bottom in the grid. -->
                                        <div class="flex flex-col gap-2 text-sm" :data-test="`import-bar-${project.trustId}`">
                                            <div class="flex items-baseline justify-between">
                                                <span
                                                    class="font-heading font-semibold text-2xl leading-none"
                                                    :class="rowTotal(project) === 0
                                                        ? 'text-gray-400 dark:text-gray-500'
                                                        : 'text-gray-900 dark:text-gray-100'"
                                                    :data-test="`pct-retrieved-${project.trustId}`"
                                                >
                                                    {{ rowTotal(project) === 0
                                                        ? "—"
                                                        : Math.round(rowRatio(project) * 100) + "%" }}
                                                </span>
                                                <span class="font-mono text-[11px] text-gray-500 dark:text-gray-400">
                                                    {{ rowTotal(project) === 0
                                                        ? "no imports yet"
                                                        : `${project.importStatus?.successful ?? 0} / ${rowTotal(project)}` }}
                                                </span>
                                            </div>
                                            <div class="flex h-2 rounded-full overflow-hidden bg-gray-100 dark:bg-gray-700">
                                                <template v-if="rowTotal(project) > 0">
                                                    <div
                                                        v-if="(project.importStatus?.successful ?? 0) > 0"
                                                        class="bg-emerald-500 transition-all"
                                                        :style="{ width: `${((project.importStatus?.successful ?? 0) / rowTotal(project)) * 100}%` }"
                                                    />
                                                    <div
                                                        v-if="(project.importStatus?.processing ?? 0) > 0"
                                                        class="bg-sky-500 transition-all"
                                                        :style="{ width: `${((project.importStatus?.processing ?? 0) / rowTotal(project)) * 100}%` }"
                                                    />
                                                    <div
                                                        v-if="(project.importStatus?.queued ?? 0) > 0"
                                                        class="bg-gray-400 transition-all"
                                                        :style="{ width: `${((project.importStatus?.queued ?? 0) / rowTotal(project)) * 100}%` }"
                                                    />
                                                    <div
                                                        v-if="rowFailed(project) > 0"
                                                        class="bg-red-500 transition-all"
                                                        :style="{ width: `${(rowFailed(project) / rowTotal(project)) * 100}%` }"
                                                    />
                                                </template>
                                            </div>
                                            <div class="flex justify-between font-mono text-[9px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
                                                <span class="inline-flex items-center gap-1">
                                                    <span class="inline-block w-1.5 h-1.5 rounded-sm bg-emerald-500" />
                                                    <span
                                                        class="text-gray-700 dark:text-gray-200 font-semibold"
                                                        :data-test="`successful-imports-${project.trustId}`"
                                                    >{{ project.importStatus?.successful ?? 0 }}</span>
                                                    retrieved
                                                </span>
                                                <span class="inline-flex items-center gap-1">
                                                    <span class="inline-block w-1.5 h-1.5 rounded-sm bg-sky-500" />
                                                    <span
                                                        class="text-gray-700 dark:text-gray-200 font-semibold"
                                                        :data-test="`processing-imports-${project.trustId}`"
                                                    >{{ project.importStatus?.processing ?? 0 }}</span>
                                                    processing
                                                </span>
                                                <span class="inline-flex items-center gap-1">
                                                    <span class="inline-block w-1.5 h-1.5 rounded-sm bg-gray-400" />
                                                    <span
                                                        class="text-gray-700 dark:text-gray-200 font-semibold"
                                                        :data-test="`queued-imports-${project.trustId}`"
                                                    >{{ project.importStatus?.queued ?? 0 }}</span>
                                                    queued
                                                </span>
                                                <span class="inline-flex items-center gap-1">
                                                    <span class="inline-block w-1.5 h-1.5 rounded-sm bg-red-500" />
                                                    <span
                                                        class="text-gray-700 dark:text-gray-200 font-semibold"
                                                        :data-test="`failed-imports-${project.trustId}`"
                                                    >{{ rowFailed(project) }}</span>
                                                    failed
                                                </span>
                                            </div>
                                        </div>
                                        <!-- Footer: project creation state (design ref:
                                             06_imaging_status IPSCreationState) -->
                                        <div class="flex items-center px-3 py-2 mt-1 -mx-4 -mb-4 border-t border-gray-100 bg-gray-50 dark:border-gray-700 dark:bg-gray-800/60">
                                            <span
                                                v-if="project.projectCreationCompleted"
                                                class="inline-flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300"
                                            >
                                                <span
                                                    class="inline-flex items-center justify-center w-4 h-4 rounded-full bg-emerald-500 text-white"
                                                    :data-test="`project-creation-complete-${project.trustId}`"
                                                >
                                                    <icon-heroicons-solid-check class="w-3 h-3" />
                                                </span>
                                                Created
                                            </span>
                                            <span
                                                v-else
                                                class="inline-flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400"
                                                :data-test="`project-creation-incomplete-${project.trustId}`"
                                            >
                                                <span class="inline-block w-1.5 h-1.5 rounded-full bg-amber-500" />
                                                Awaiting creation…
                                            </span>
                                        </div>
                                    </div>
                                </li>
                            </ul>
                            <template v-if="canLoad">
                                <div v-if="sortedData?.length === 0" class="flex flex-row items-center h-full">
                                    <p class="flex items-center justify-center gap-2 flex-1 text-center" data-test="no-project-status-message">
                                        <icon-heroicons-outline-clock class="w-5 h-5" />
                                        Awaiting imaging project creation from trusts…
                                    </p>
                                </div>
                            </template>
                        </div>
                    </div>
                </div>
            </Transition>
        </div>
        <template v-else>
            <AiAlert
                text="Project approval is required to view the imaging project status"
                variant="info"
                close
                class="relative m-auto text-base"
                :rounded="false"
                :bordered="false"
            />
            <div class="relative flex flex-col items-center justify-center">
                <div class="flex w-full p-4 grow">
                    <div class="flex w-full gap-2 grow">
                        <div class="flex w-full gap-2">
                            <div v-for="trust in 3" :key="trust" class="w-1/3">
                                <div class="flex items-center gap-2">
                                    <AiSkeleton class="h-8 animate-none" />
                                </div>
                                <div class="flex items-center gap-2">
                                    <AiSkeleton class="h-8 animate-none" />
                                </div>
                            </div>

                            <div class="w-2/5 space-y-4 bg-gray-200 dark:bg-gray-700" />
                        </div>
                    </div>
                </div>
                <div>
                    <div class="absolute inset-0 top-0 w-full h-full backdrop-blur-sm" />
                </div>
            </div>
        </template>
    </AiCard>
</template>

<script setup lang="ts">
import { ExclamationCircleIcon, RefreshIcon } from "@heroicons/vue/solid";
import useSWRV from "swrv";
import { sortBy } from "underscore";
import { computed, watch } from "vue";
import { useRoute } from "vue-router";

import AiAlert from "@/components/AiAlert/AiAlert.vue";
import AiSkeleton from "@/components/AiSkeleton/AiSkeleton.vue";
import useErrorHandler from "@/composables/useErrorHandler";
import { getImagingProjectsStatus, IImagingProjectStatus } from "@/services/project-service";
import { useSiteDetailsStore } from "@/store/siteDetailsStore";

interface IImagingProjectStatusProps {
    canLoad: boolean;
    cohortSize?: number;
}

interface IImagingProjectOverview {
    projectCreationCompleted: number;
    projectCreationTotal: number;
    studyRetrievalTotal: number;
    processingTotal: number;
    queuedTotal: number;
    failedTotal: number;
    importKnownTotal: number;
}

const props = defineProps<IImagingProjectStatusProps>();
const route = useRoute();

const { data, error } = useSWRV(
    () => {
        if (!props.canLoad) {
            return "";
        }

        return `/projects/${route.params.projectId}/image/status`;
    },
    getImagingProjectsStatus,
    {
        refreshInterval: 10_000,
        dedupingInterval: 5_000,
        shouldRetryOnError: false
    }
);

useErrorHandler(error);

watch(() => route.params.projectId, () => {
    data.value = undefined;
}, { flush: "sync" });

const sortedData = computed(() =>
    sortBy(
        data.value ?? [],
        "trustName"
    )
);

const overview = computed<IImagingProjectOverview>(() => {
    const statuses = data.value ?? [];
    const totals = statuses.reduce((previous, current) => {
        const successful = current.importStatus?.successful ?? 0;
        const processing = current.importStatus?.processing ?? 0;
        const queued = current.importStatus?.queued ?? 0;
        const failed = (current.importStatus?.failed ?? 0) + (current.importStatus?.queueFailed ?? 0);

        return {
            studyRetrievalTotal: previous.studyRetrievalTotal + successful,
            processingTotal: previous.processingTotal + processing,
            queuedTotal: previous.queuedTotal + queued,
            failedTotal: previous.failedTotal + failed,
            importKnownTotal: previous.importKnownTotal + successful + processing + queued + failed
        };
    }, {
        studyRetrievalTotal: 0,
        processingTotal: 0,
        queuedTotal: 0,
        failedTotal: 0,
        importKnownTotal: 0
    });

    return {
        projectCreationCompleted: statuses.filter(trust => trust.projectCreationCompleted).length,
        projectCreationTotal: statuses.length,
        ...totals
    };
});

const overviewRetrievalPercent = computed(() => {
    if (!props.cohortSize) return 0;

    return Math.round((overview.value.studyRetrievalTotal / props.cohortSize) * 100);
});

const formatCount = (value: number): string => value.toLocaleString();
const expectedCohortLabel = computed(() => props.cohortSize?.toLocaleString() ?? "unknown");

// Per-trust import totals/ratio for the stacked-bar UI (design ref:
// 06_imaging_status ImagingStatusA / IPSImportBar). `failed` rolls
// queueFailed + failed together because both indicate "import didn't
// land", which is what a viewer wants to see in one chunk of the bar.
const rowTotal = (p: IImagingProjectStatus): number => {
    const s = p.importStatus;
    if (!s) return 0;

    return (s.successful ?? 0) + (s.processing ?? 0) + (s.queued ?? 0) + (s.failed ?? 0) + (s.queueFailed ?? 0);
};
const rowFailed = (p: IImagingProjectStatus): number => {
    const s = p.importStatus;
    if (!s) return 0;

    return (s.failed ?? 0) + (s.queueFailed ?? 0);
};
const rowRatio = (p: IImagingProjectStatus): number => {
    const t = rowTotal(p);

    return t === 0 ? 0 : (p.importStatus?.successful ?? 0) / t;
};

const ReimportCountLimitMessage = "The max reimport count has been reached. Any failed studies will not be reimported. Please contact an XNAT administrator for assistance.";

// Backend is the single source of truth — /site/details returns
// MAX_REIMPORT_COUNT from the flip-api Settings, and the same number
// gates the SQL reimport query (project_services.get_reimport_queries_service).
// The store value is undefined until the first /site/details fetch
// lands; fall back to 0 so the "at cap" branch doesn't prematurely show
// a limit-reached warning before we actually know what the limit is.
const siteDetailsStore = useSiteDetailsStore();
const maxReimportCount = computed(() => siteDetailsStore.maxReimportCount ?? 0);
</script>
