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
                        class="mt-1 text-[13px] leading-snug text-gray-500 dark:text-gray-300"
                    >
                        When this project was approved, an XNAT project was created at each trust.
                        Live import counts below.
                    </p>
                </div>
            </div>
        </div>
        <CohortSnapshotSummary :can-load="canLoad" />
        <div v-if="canLoad" class="flex-grow text-sm" data-test="project-status-container">
            <Transition name="fade" mode="out-in">
                <div v-if="!data" class="p-4 space-y-2 transition">
                    <AiSkeleton class="w-1/4 h-8" />
                    <AiSkeleton class="w-full h-8 mt-2" />
                    <AiSkeleton class="w-full h-8" />
                    <AiSkeleton class="w-full h-8" />
                </div>

                <div v-else class="space-y-4">
                    <div class="grid grid-cols-5 overflow-hidden border-t border-b border-gray-200 divide-x divide-gray-200 dark:border-dark-border dark:divide-dark-border">
                        <div class="min-w-0 p-2.5">
                            <p class="font-mono text-[10px] uppercase tracking-wide leading-tight text-gray-500 dark:text-gray-300 break-words">
                                Trusts onboarded
                            </p>
                            <p class="mt-1.5 text-xl font-semibold leading-none font-heading text-gray-900 dark:text-gray-100" data-test="overview-project-creation">
                                {{ overview.projectCreationCompleted }}/{{ overview.projectCreationTotal }}
                            </p>
                            <p class="mt-1.5 text-[11px] leading-tight text-gray-500 dark:text-gray-300">
                                imaging projects created
                            </p>
                        </div>
                        <div class="min-w-0 p-2.5">
                            <p class="font-mono text-[10px] uppercase tracking-wide leading-tight text-gray-500 dark:text-gray-300 break-words">
                                Studies retrieved
                            </p>
                            <p class="mt-1.5 text-xl font-semibold leading-none font-heading text-gray-900 dark:text-gray-100" data-test="overview-image-retrieval">
                                {{ formatCount(overview.studyRetrievalTotal) }}
                            </p>
                            <p class="mt-1.5 text-[11px] leading-tight text-gray-500 dark:text-gray-300" data-test="overview-retrieval-percent">
                                {{ overviewRetrievalPercent }}% of expected {{ expectedCohortLabel }}
                            </p>
                        </div>
                        <div class="min-w-0 p-2.5">
                            <p class="font-mono text-[10px] uppercase tracking-wide leading-tight text-gray-500 dark:text-gray-300 break-words">
                                Processing
                            </p>
                            <p class="mt-1.5 text-xl font-semibold leading-none font-heading text-gray-900 dark:text-gray-100">
                                {{ formatCount(overview.processingTotal) }}
                            </p>
                            <p class="mt-1.5 text-[11px] leading-tight text-gray-500 dark:text-gray-300">
                                in flight at trusts
                            </p>
                        </div>
                        <div class="min-w-0 p-2.5">
                            <p class="font-mono text-[10px] uppercase tracking-wide leading-tight text-gray-500 dark:text-gray-300 break-words">
                                Queued
                            </p>
                            <p class="mt-1.5 text-xl font-semibold leading-none font-heading text-gray-900 dark:text-gray-100">
                                {{ formatCount(overview.queuedTotal) }}
                            </p>
                            <p class="mt-1.5 text-[11px] leading-tight text-gray-500 dark:text-gray-300">
                                waiting on import workers
                            </p>
                        </div>
                        <div class="min-w-0 p-2.5">
                            <p class="font-mono text-[10px] uppercase tracking-wide leading-tight text-gray-500 dark:text-gray-300 break-words">
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
                            <p class="mt-1.5 text-[11px] leading-tight text-gray-500 dark:text-gray-300">
                                {{ overview.failedTotal > 0 ? "requires attention" : "no errors" }}
                            </p>
                        </div>
                    </div>

                    <div class="w-full border-t border-gray-200 dark:border-dark-border">
                        <div class="w-full">
                            <ul role="list" class="grid grid-cols-1 gap-3 p-3 xl:grid-cols-2">
                                <li
                                    v-for="project in sortedData"
                                    :key="project.trustId"
                                    class="relative p-4 border rounded-lg hover:bg-gray-50 dark:hover:bg-dark-surface"
                                    :class="isImportStale(project)
                                        ? 'border-red-400 ring-[3px] ring-red-500/10 dark:border-red-500/70 dark:ring-red-500/15'
                                        : 'border-gray-200 dark:border-dark-border'"
                                    :data-test="`trust-card-${project.trustId}`"
                                >
                                    <div class="flex flex-col gap-3">
                                        <!-- Top row: trust name + reimport count -->
                                        <div class="flex items-center justify-between gap-3">
                                            <h2 class="font-bold font-heading text-base text-gray-900 dark:text-gray-100" :data-test="`trust-name-${project.trustId}`">
                                                {{ project.trustName }}
                                            </h2>
                                            <div
                                                v-if="project.reimportCount !== undefined && project.reimportCount !== null && project.projectCreationCompleted && maxReimportCount > 0"
                                                v-tippy="{ content: reimportTooltip(project), placement: 'top' }"
                                                class="flex items-center gap-1.5 shrink-0"
                                            >
                                                <icon-ph-arrows-clockwise
                                                    v-if="project.reimportCount < maxReimportCount"
                                                    class="w-4 h-4"
                                                    :class="isImportStale(project) ? 'text-gray-400 dark:text-gray-300' : 'text-green-500'"
                                                />
                                                <icon-ph-warning-circle-fill
                                                    v-else
                                                    class="w-4 h-4 text-yellow-500"
                                                />
                                                <span
                                                    class="font-mono text-xs text-gray-500 dark:text-gray-300"
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
                                            <div class="flex items-baseline justify-between gap-2">
                                                <span class="inline-flex items-baseline gap-2 min-w-0">
                                                    <span
                                                        class="font-heading font-semibold text-2xl leading-none"
                                                        :class="rowTotal(project) === 0
                                                            ? 'text-gray-400 dark:text-gray-300'
                                                            : 'text-gray-900 dark:text-gray-100'"
                                                        :data-test="`pct-retrieved-${project.trustId}`"
                                                    >
                                                        {{ rowTotal(project) === 0
                                                            ? "—"
                                                            : floorPercent(rowRatio(project)) + "%" }}
                                                    </span>
                                                    <!-- The counts are real but no longer confirmed; say so next to
                                                         the number rather than letting it read as live. -->
                                                    <span
                                                        v-if="isImportStale(project) && rowTotal(project) > 0"
                                                        class="font-mono text-[9px] uppercase tracking-[0.08em] rounded px-1.5 py-0.5 border border-red-200 bg-red-50 text-red-700 dark:border-red-500/40 dark:bg-red-500/10 dark:text-red-300"
                                                        :data-test="`last-known-tag-${project.trustId}`"
                                                    >
                                                        Last known
                                                    </span>
                                                </span>
                                                <span
                                                    class="font-mono text-[11px] text-gray-500 dark:text-gray-300 shrink-0"
                                                    :data-test="`import-count-${project.trustId}`"
                                                >
                                                    {{ importCountLabel(project) }}
                                                </span>
                                            </div>
                                            <div
                                                class="flex h-2 rounded-full overflow-hidden bg-gray-100 dark:bg-dark-raised"
                                                :class="{ 'opacity-[0.55]': isImportStale(project) }"
                                            >
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
                                            <div
                                                class="flex justify-between font-mono text-[9px] uppercase tracking-wide text-gray-500 dark:text-gray-300"
                                                :class="{ 'opacity-[0.65]': isImportStale(project) }"
                                            >
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
                                             06_imaging_status IPSCreationState), or the import
                                             error state when the counts above have gone stale. -->
                                        <div
                                            v-if="importError(project)"
                                            class="flex items-start gap-2 px-3 py-2 mt-1 -mx-4 -mb-4 border-t border-red-200 bg-red-50 dark:border-red-500/40 dark:bg-red-500/10"
                                            :data-test="`import-error-${project.trustId}`"
                                        >
                                            <icon-ph-x-circle-fill
                                                v-if="project.connectionState === 'project-missing'"
                                                class="w-4 h-4 shrink-0 mt-px text-red-600 dark:text-red-400"
                                            />
                                            <icon-ph-warning-circle-fill
                                                v-else
                                                class="w-4 h-4 shrink-0 mt-px text-red-600 dark:text-red-400"
                                            />
                                            <span class="flex flex-col gap-0.5 min-w-0">
                                                <span class="text-xs font-bold text-red-700 dark:text-red-300">
                                                    {{ importError(project)?.title }}
                                                </span>
                                                <span class="text-[11px] leading-[1.4] text-red-600 dark:text-red-400">
                                                    {{ importError(project)?.detail }}
                                                </span>
                                            </span>
                                        </div>
                                        <div v-else class="flex items-center px-3 py-2 mt-1 -mx-4 -mb-4 border-t border-gray-100 bg-gray-50 dark:border-dark-border dark:bg-dark-surface/60">
                                            <span
                                                v-if="project.projectCreationCompleted"
                                                class="inline-flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300"
                                            >
                                                <span
                                                    class="inline-flex items-center justify-center w-4 h-4 rounded-full bg-emerald-500 text-white"
                                                    :data-test="`project-creation-complete-${project.trustId}`"
                                                >
                                                    <icon-ph-check-bold class="w-3 h-3" />
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
                                        <icon-ph-clock class="w-5 h-5" />
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

                            <div class="w-2/5 space-y-4 bg-gray-200 dark:bg-dark-raised" />
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
import useSWRV from "swrv";
import { sortBy } from "underscore";
import { computed, watch } from "vue";
import { useRoute } from "vue-router";

import AiAlert from "@/components/AiAlert/AiAlert.vue";
import AiSkeleton from "@/components/AiSkeleton/AiSkeleton.vue";
import useErrorHandler from "@/composables/useErrorHandler";
import { getImagingProjectsStatus, IImagingProjectStatus } from "@/services/project-service";
import { useSiteDetailsStore } from "@/store/siteDetailsStore";

import CohortSnapshotSummary from "./CohortSnapshotSummary.vue";

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

    return floorPercent(overview.value.studyRetrievalTotal / props.cohortSize);
});

const formatCount = (value: number): string => value.toLocaleString();
const expectedCohortLabel = computed(() => props.cohortSize?.toLocaleString() ?? "unknown");
const floorPercent = (ratio: number): number => Math.floor(ratio * 100);

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

// Import error states (FLIP#1022). The counts a trust reports are always the *last known*
// ones; `connectionState` says whether they are still current. A trust that has dropped out
// keeps its last percentage on screen, marked stale, rather than collapsing to zero — zero
// would read as "the data went away", which is the opposite of what happened.
//
// `connectionState` is optional so a hub that predates the field renders as healthy rather
// than flagging every card.
const isImportStale = (p: IImagingProjectStatus): boolean =>
    p.connectionState === "unreachable" || p.connectionState === "project-missing";

// Copy per state. The two states send an admin to different places — one is a connectivity
// problem at the Trust, the other is a data problem inside a reachable XNAT — so the titles
// contrast reachability explicitly rather than both reading as "can't find the project".
// `unreachable` covers everything that stopped a refresh landing except a confirmed-missing
// project, so its wording stays about the connection rather than the project.
const ImportErrorCopy = {
    "unreachable": {
        title: "Trust XNAT not reachable",
        detail: "XNAT at this Trust did not respond — check the connection. Showing last known counts.",
        reimportTooltip: "Reimport attempts — paused while the Trust is unreachable"
    },
    "project-missing": {
        title: "Trust XNAT reachable, but project not found",
        detail: "XNAT responded but this imaging project no longer exists at the Trust. "
            + "Contact an XNAT administrator. Showing last known counts.",
        reimportTooltip: "Reimport attempts — imaging project not found"
    }
} as const;

const importError = (p: IImagingProjectStatus) => {
    const state = p.connectionState;

    return state === "unreachable" || state === "project-missing" ? ImportErrorCopy[state] : null;
};

// Short local time the counts were last confirmed, e.g. "13:58". Empty when the trust has
// never reported successfully (nothing to be stale *since*) or the timestamp is unparseable,
// so the caller can omit the "at …" suffix entirely rather than print "at Invalid Date".
const lastSeenTime = (p: IImagingProjectStatus): string => {
    if (!p.lastSeenAt) return "";
    const parsed = new Date(p.lastSeenAt);

    return Number.isNaN(parsed.getTime())
        ? ""
        : parsed.toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
            hour12: false
        });
};

// The mono count on the right of the percentage row, with the last-confirmed time appended
// while the counts are stale.
const importCountLabel = (p: IImagingProjectStatus): string => {
    if (rowTotal(p) === 0) return "no imports yet";
    const counts = `${p.importStatus?.successful ?? 0} / ${rowTotal(p)}`;
    const seen = isImportStale(p) ? lastSeenTime(p) : "";

    return seen ? `${counts} at ${seen}` : counts;
};

const reimportTooltip = (p: IImagingProjectStatus): string => {
    const error = importError(p);
    if (error) return error.reimportTooltip;

    return p.reimportCount !== undefined && p.reimportCount >= maxReimportCount.value
        ? ReimportCountLimitMessage
        : "Reimport attempts";
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
