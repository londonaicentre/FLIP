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

<!-- eslint-disable vue/multi-word-component-names -->
<route lang="yaml">
    name: Models
</route>

<template>
    <div class="flex flex-col w-full h-full">
        <transition name="fade" mode="out-in">
            <AiLoader v-if="!data?.data" />
            <div v-else class="flex flex-col flex-1 min-w-0 overflow-y-auto">
                <!-- Page header (mirrors the Projects page spine) -->
                <header class="flex flex-col gap-4 px-8 pt-8 pb-4 sm:flex-row sm:items-end sm:justify-between">
                    <div>
                        <p
                            class="text-xs text-gray-500 dark:text-gray-300"
                            data-test="models-scope-eyebrow"
                        >
                            <template v-if="isScoped">
                                Project ·
                                <!-- font-normal keeps the anchor at the eyebrow's weight (main.css
                                     bolds every non-nav link); the underline carries the affordance. -->
                                <router-link
                                    :to="`/project/${projectFilter}`"
                                    data-test="scope-project-link"
                                    class="font-normal hover:underline hover:text-primary-600 dark:hover:text-primary-300"
                                >
                                    {{ scopedProjectLabel }}
                                </router-link>
                            </template>
                            <template v-else>
                                Estate-wide · every project
                            </template>
                        </p>
                        <h1 class="text-3xl font-semibold font-heading mt-1 text-gray-900 dark:text-gray-100">
                            <span class="text-primary-600 underline decoration-4 decoration-primary-500/60 underline-offset-8 dark:text-white">Models</span>
                            <span class="ml-3 text-gray-500 dark:text-gray-300 font-medium">{{ data.totalRecords ?? data.data.length }}</span>
                        </h1>
                        <p class="mt-2 text-sm text-gray-500 dark:text-gray-300">
                            {{ isScoped ? SCOPED_SUBTITLE : ESTATE_SUBTITLE }}
                        </p>
                    </div>
                    <!-- Only offered when scoped: a model is created against one project, and the
                         estate-wide view has none in hand. Server-side, creating against an
                         unapproved project is refused, so don't offer it here either. -->
                    <AiButton
                        v-if="isScoped && !isViewer && scopedProject?.status === 'APPROVED'"
                        primary
                        data-test="add-model-btn"
                        aria-label="Create Model"
                        tooltip="Create Model"
                        @click="modalsStore.toggleCreateModel"
                    >
                        <icon-mdi-plus class="lg:mr-2" />
                        <span class="hidden lg:inline">Create Model</span>
                    </AiButton>
                </header>

                <!-- Status filter: one group at a time, shown as tiles from md up and as a segmented row on
                     phones. Both drive the same selection; All keeps the hub's newest-activity order. -->
                <div class="hidden md:flex flex-wrap gap-3 px-8 pb-4">
                    <button
                        v-for="group in groupOptions"
                        :key="group.key"
                        type="button"
                        :data-test="`filter-tile-${group.key}`"
                        :aria-pressed="statusFilter === group.key"
                        class="flex-1 min-w-[120px] rounded-xl border px-4 py-3 text-left transition"
                        :class="statusFilter === group.key
                            ? [TILE_SELECTED_CLASS[group.key], 'ring-[3px]']
                            : TILE_IDLE_CLASS"
                        @click="selectStatus(statusFilter === group.key ? 'all' : group.key)"
                    >
                        <div class="flex items-center gap-2">
                            <span class="inline-block w-2 h-2 rounded-full" :class="statusDotClass(group.statuses[0])" />
                            <span class="text-xs text-gray-500 dark:text-gray-300">
                                {{ group.label }}
                            </span>
                        </div>
                        <div
                            :data-test="`filter-tile-count-${group.key}`"
                            class="mt-1 text-2xl font-bold tabular-nums text-gray-900 dark:text-gray-100"
                        >
                            {{ group.count }}
                        </div>
                    </button>
                </div>
                <div class="px-8 pb-4 md:hidden">
                    <div
                        role="tablist"
                        aria-label="Model status"
                        data-test="status-filter"
                        :class="[SEGMENTED_GROUP_CLASS, 'flex-wrap']"
                    >
                        <button
                            v-for="option in statusOptions"
                            :key="option.key"
                            type="button"
                            role="tab"
                            :aria-selected="statusFilter === option.key"
                            :data-test="`status-filter-${option.key}`"
                            :class="segmentClass(statusFilter === option.key)"
                            @click="selectStatus(option.key)"
                        >
                            {{ option.label }}
                            <span
                                :data-test="`status-filter-count-${option.key}`"
                                class="tabular-nums"
                                :class="option.count ? 'text-gray-900 dark:text-gray-100' : 'font-normal'"
                            >{{ option.count }}</span>
                        </button>
                    </div>
                </div>

                <!-- Toolbar: search + project filter -->
                <div class="flex flex-wrap items-center gap-3 px-8 pb-4">
                    <div class="flex-1 min-w-[240px]">
                        <AiSearch
                            v-model="search"
                            placeholder="Search models or projects…"
                            data-test="model-search"
                        />
                    </div>

                    <!-- Stands down once a project is picked: the chip beside it already names the
                         project and carries the way out, so two controls for one scope is one too
                         many. Clearing the chip brings the select back. -->
                    <label v-if="!isScoped" for="project-filter" class="sr-only">Filter by project</label>
                    <!-- Bound to the URL rather than v-model'd: the address bar owns the scope, so
                         the select reflects it instead of holding a second copy of it. -->
                    <select
                        v-if="!isScoped"
                        id="project-filter"
                        data-test="project-filter"
                        class="rounded-md border-gray-200 dark:border-dark-border dark:bg-dark-canvas text-sm py-2 pl-3 pr-8"
                        :value="projectFilter ?? ''"
                        @change="applyProject(($event.target as HTMLSelectElement).value || null)"
                    >
                        <option value="">
                            All projects
                        </option>
                        <option v-for="project in projectOptionList" :key="project.id" :value="project.id">
                            {{ project.name }}
                        </option>
                    </select>

                    <span
                        v-if="isScoped"
                        data-test="project-filter-chip"
                        class="inline-flex items-center gap-2 rounded-full border border-primary-200 bg-primary-100 py-1.5 pl-4 pr-1.5 text-note font-semibold text-primary-600 dark:border-dark-border dark:bg-dark-surface dark:text-primary-200"
                    >
                        Project: {{ scopedProjectLabel }}
                        <button
                            type="button"
                            data-test="clear-project-filter"
                            aria-label="Clear project filter"
                            title="Clear project filter"
                            class="inline-flex items-center justify-center w-5 h-5 rounded-full bg-primary-200 hover:bg-primary-300 dark:bg-dark-raised dark:hover:bg-dark-border"
                            @click="applyProject(null)"
                        >
                            <icon-mdi-close class="w-3.5 h-3.5" />
                        </button>
                    </span>
                </div>

                <!-- Models table (div/grid layout: white rows + status rail, escapes the global
                     `<table>` styling in main.css). Same surface as the Projects list: an AiCard
                     holding white rows divided by hairlines, so the two tables read as one system. -->
                <div class="px-8 pb-8">
                    <AiCard class="overflow-hidden p-0">
                        <!-- Header row: sortable columns (desktop-only — the stacked
                             mobile rows have no columns to head) -->
                        <div class="hidden sm:flex items-stretch bg-gray-50 border-b border-gray-200 dark:bg-dark-surface dark:border-dark-border">
                            <div class="w-[3px] shrink-0" />
                            <div :class="GRID_CLASS" class="grid flex-1 gap-4 px-6 py-3">
                                <button
                                    v-for="col in columns"
                                    :key="col.label"
                                    type="button"
                                    :data-test="col.key ? `sort-header-${col.key}` : undefined"
                                    class="flex items-center gap-1 text-xs font-medium text-gray-500 dark:text-gray-300 select-none"
                                    :class="[
                                        col.align === 'right' ? 'justify-end' : 'justify-start',
                                        col.key ? 'cursor-pointer hover:text-gray-700 dark:hover:text-gray-200' : 'cursor-default'
                                    ]"
                                    @click="col.key && toggleSort(col.key)"
                                >
                                    {{ col.label }}
                                    <span
                                        v-if="col.key && sortKey === col.key"
                                        class="text-primary-600 dark:text-primary-300"
                                    >{{ sortDir === "asc" ? "↑" : "↓" }}</span>
                                </button>
                            </div>
                        </div>

                        <!-- Data rows -->
                        <div
                            v-for="(model, index) in sortedModels"
                            :key="model.id"
                            :data-test="`models-list-item-${index}`"
                            class="flex items-stretch bg-white border-t border-gray-100 transition-colors first:border-t-0 hover:bg-gray-50 dark:bg-dark-canvas dark:border-dark-border dark:hover:bg-dark-surface"
                        >
                            <!-- Status rail -->
                            <div
                                data-test="model-status-rail"
                                class="w-[3px] shrink-0"
                                :class="railClass(model.status)"
                            />
                            <div :class="GRID_CLASS" class="hidden sm:grid flex-1 items-center gap-4 px-6 py-4">
                                <!-- Model -->
                                <div class="min-w-0">
                                    <router-link
                                        :to="`/project/${model.projectId}/model/${model.id}`"
                                        data-test="model-name"
                                        class="text-sm font-semibold text-gray-900 hover:text-primary-600 dark:text-gray-100 dark:hover:text-primary-300"
                                        @click.stop
                                    >
                                        {{ model.name }}
                                    </router-link>
                                    <p
                                        v-if="metaLine(model)"
                                        data-test="model-owner-meta"
                                        class="mt-1 text-note text-gray-500 dark:text-gray-300"
                                    >
                                        {{ metaLine(model) }}
                                    </p>
                                </div>
                                <!-- Project -->
                                <div class="min-w-0">
                                    <router-link
                                        :to="`/project/${model.projectId}`"
                                        data-test="model-project"
                                        class="text-sm font-semibold text-gray-900 hover:text-primary-600 dark:text-gray-100 dark:hover:text-primary-300"
                                        @click.stop
                                    >
                                        {{ model.projectName }}
                                    </router-link>
                                </div>
                                <!-- Trusts -->
                                <div class="min-w-0">
                                    <div v-if="model.trusts.length" class="flex flex-wrap items-center gap-1.5">
                                        <span
                                            v-for="t in model.trusts"
                                            :key="t.id"
                                            data-test="model-trust-chip"
                                            :title="t.name"
                                            :class="[TRUST_CHIP_CLASS, TRUST_CHIP_PLAIN_PADDING]"
                                        >
                                            <!-- Code only, no status dot: every chip here is a trust the
                                                 run was dispatched to, so there is no state to encode. The
                                                 Projects list dots because a trust there can be pending. -->
                                            {{ trustChipLabel(t) }}
                                        </span>
                                    </div>
                                    <p
                                        v-else
                                        data-test="model-trusts-empty"
                                        class="text-sm text-gray-500 dark:text-gray-300"
                                    >
                                        Trusts assigned when training starts
                                    </p>
                                </div>
                                <!-- Status -->
                                <div class="flex justify-end">
                                    <span
                                        data-test="model-status-indicator"
                                        class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
                                        :class="statusPillClass(model.status)"
                                    >
                                        <span class="inline-block w-1.5 h-1.5 rounded-full" :class="statusDotClass(model.status)" />
                                        {{ statusLabel(model.status) }}
                                    </span>
                                </div>
                            </div>

                            <!-- Mobile: one stacked column, so nothing is pushed past the card's edge: project
                                 eyebrow, name, meta line and description, then the pill with the trust chips
                                 wrapping after it. -->
                            <div class="flex-1 min-w-0 px-4 py-3 sm:hidden">
                                <router-link
                                    :to="`/project/${model.projectId}`"
                                    data-test="model-project-eyebrow"
                                    class="block text-xs mb-[3px]
                                    truncate text-gray-500 dark:text-gray-300
                                    hover:text-primary-600 dark:hover:text-primary-300"
                                    @click.stop
                                >
                                    {{ model.projectName }}
                                </router-link>
                                <router-link
                                    :to="`/project/${model.projectId}/model/${model.id}`"
                                    data-test="model-name-mobile"
                                    class="text-sm font-semibold break-words
                                    text-gray-900 dark:text-gray-100
                                    hover:text-primary-600 dark:hover:text-primary-300"
                                    @click.stop
                                >
                                    {{ model.name }}
                                </router-link>
                                <p
                                    v-if="metaLine(model)"
                                    data-test="model-owner-meta-mobile"
                                    class="mt-1 text-note text-gray-500 dark:text-gray-300"
                                >
                                    {{ metaLine(model) }}
                                </p>
                                <p
                                    v-if="model.description !== undefined"
                                    data-test="model-description-mobile"
                                    class="mt-1 text-note leading-snug break-words line-clamp-2
                                    text-gray-500 dark:text-gray-300"
                                >
                                    {{ model.description || "No description provided..." }}
                                </p>
                                <div class="flex flex-wrap items-center gap-1.5 mt-2">
                                    <span
                                        data-test="model-status-indicator-mobile"
                                        class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
                                        :class="statusPillClass(model.status)"
                                    >
                                        <span class="inline-block w-1.5 h-1.5 rounded-full" :class="statusDotClass(model.status)" />
                                        {{ statusLabel(model.status) }}
                                    </span>
                                    <span
                                        v-for="t in model.trusts"
                                        :key="t.id"
                                        data-test="model-trust-chip-mobile"
                                        :title="t.name"
                                        :class="[TRUST_CHIP_CLASS, TRUST_CHIP_PLAIN_PADDING]"
                                    >
                                        {{ trustChipLabel(t) }}
                                    </span>
                                    <span
                                        v-if="!model.trusts.length"
                                        data-test="model-trusts-empty-mobile"
                                        class="text-note text-gray-500 dark:text-gray-300"
                                    >
                                        Trusts assigned when training starts
                                    </span>
                                </div>
                            </div>
                        </div>

                        <div
                            v-if="!sortedModels.length"
                            data-test="models-empty"
                            class="flex flex-wrap items-center justify-center gap-x-1 px-6 py-12 text-sm
                            text-gray-500 dark:text-gray-300"
                        >
                            {{ emptyMessage }}
                            <AiButton
                                v-if="statusFilter !== 'all'"
                                text-primary
                                data-test="show-all-models"
                                @click="selectStatus('all')"
                            >
                                Show all models
                            </AiButton>
                        </div>
                    </AiCard>

                    <!-- Nothing to page through on a single page. -->
                    <AiPagination
                        v-if="(data?.totalPages ?? 1) > 1"
                        class="mt-4"
                        :total-pages="data?.totalPages ?? 1"
                        :current-page="data?.page ?? 1"
                        @page-update="updateModelList"
                    />
                </div>
            </div>
        </transition>
        <CreateModelModal
            v-if="isScoped && projectFilter"
            :open="modalsStore.createModelOpen"
            :project-id="projectFilter"
            @close-modal="modalsStore.toggleCreateModel"
        />
    </div>
</template>

<script setup lang="ts">
import { debouncedWatch } from "@vueuse/core";
import useSWRV from "swrv";
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiCard from "@/components/AiCard/AiCard.vue";
import AiLoader from "@/components/AiLoader/AiLoader.vue";
import AiPagination from "@/components/AiPagination/AiPagination.vue";
import AiSearch from "@/components/AiSearch/AiSearch.vue";
import useErrorHandler from "@/composables/useErrorHandler";
import { usePermissions } from "@/composables/usePermissions";
import CreateModelModal from "@/partials/models/CreateModelModal.vue";
// Status pill/dot classes, shared with the mobile project-models list; imported under their historical local names.
import { getAllModels,
    getModelProjectOptions,
    IModelProjectOption,
    IModelSummary,
    MODEL_STATUS_GROUPS,
    ModelStatus,
    modelStatusDotClass as statusDotClass,
    modelStatusGroup,
    ModelStatusGroupKey,
    modelStatusLabel,
    modelStatusPillClass as statusPillClass } from "@/services/model-service";
import { useModalsStore } from "@/store/modals";
import { apiTimestampMs, relativeAgeLabel } from "@/utils/helpers";
import { segmentClass, SEGMENTED_GROUP_CLASS } from "@/utils/segmented";
import { Snackbar } from "@/utils/snackbar";
import { TRUST_CHIP_CLASS, TRUST_CHIP_PLAIN_PADDING, trustChipLabel } from "@/utils/trust-chip";

const pageSize = 20;

const ESTATE_SUBTITLE = "The federated training queue across every project you can access.";
const SCOPED_SUBTITLE =
    "The federated training queue for this project. Clear the project filter to see the whole estate.";

const route = useRoute();
const router = useRouter();
const modalsStore = useModalsStore();
const { isViewer } = usePermissions();

// The project scope lives in the URL, not in a ref: a scoped view is then linkable, the back
// button works, and the fetch key below reads the same source of truth the address bar shows.
const projectFilter = computed<string | null>(() => {
    const raw = route.query.project;
    const value = Array.isArray(raw) ? raw[0] : raw;

    return value ? String(value) : null;
});
const projectQueryParam = computed<string>(() =>
    projectFilter.value ? `&project=${encodeURIComponent(projectFilter.value)}` : "");

const search = ref("");
const pageNumber = ref(1);
const searchQueryParam = ref("");

type StatusFilter = ModelStatusGroupKey | "all";
const statusFilter = ref<StatusFilter>("all");
const activeGroup = computed(() => MODEL_STATUS_GROUPS.find(group => group.key === statusFilter.value));
const statusQueryParam = computed<string>(() =>
    activeGroup.value ? `&status=${activeGroup.value.statuses.join(",")}` : "");

const selectStatus = (key: StatusFilter): void => {
    statusFilter.value = key;
    pageNumber.value = 1;
};

// Which project the page believes it is showing. Changing the dropdown navigates, so the
// watcher below would otherwise treat the page's own navigation as an arrival and wipe the
// filters the user just set. Claiming the value first tells the two apart. (A one-shot boolean
// would not: it stays armed if `replace` rejects a duplicate navigation.)
const claimedProject = ref<string | null>(projectFilter.value);

const applyProject = (id: string | null): void => {
    claimedProject.value = id;
    pageNumber.value = 1;
    const query = { ...route.query };
    if (id) {
        query.project = id;
    } else {
        delete query.project;
    }
    void router.replace({
        path: route.path,
        query
    });
};

// An arrival from elsewhere — the sidebar Models link, a bookmark, back/forward. The link is a
// query-only change on this same route, so the component is never remounted and only this can
// reset the page.
watch(projectFilter, next => {
    if (next === claimedProject.value) return;
    claimedProject.value = next;
    selectStatus("all");
    if (!next) {
        // Both, deliberately: the debouncedWatch on `search` fires 500ms later, far too late
        // for the key this reset is about to rebuild.
        search.value = "";
        searchQueryParam.value = "";
    }
});

// The filter's options, and the only place the scoped project's name and status come from — a
// project with no models yet has no row in the list to read them off. Barely changes, so it
// polls not at all and dedupes for a minute. `mutate` forces a fetch through that dedup window
// (see the guard watcher below); `isValidating` says when one is in flight.
const {
    data: projectOptions,
    error: projectOptionsError,
    isValidating: projectOptionsValidating,
    mutate: revalidateProjectOptions
} = useSWRV("/models/projects", getModelProjectOptions, {
    dedupingInterval: 60_000,
    shouldRetryOnError: false
});

// The options as a list, whatever arrived. Array-checked rather than `?? []`: the body comes off
// the wire, and a catch-all stub answering with an empty string would otherwise reach `.find()`
// and take the whole page down with a TypeError.
const projectOptionList = computed<IModelProjectOption[]>(() =>
    Array.isArray(projectOptions.value) ? projectOptions.value : []);

// With `?project=` in the URL, hold the models fetch until the options have confirmed the id: a
// stale or unauthorised link would otherwise fire a scoped call the backend 403s/404s, flashing
// the global error banner in the gap before the guard below can tidy the URL. An options
// *failure* opens the gate instead — the list must still load rather than wait on a gate that can
// never open, with the filter degrading to invisible.
const scopedFetchReady = computed<boolean>(() => {
    if (!projectFilter.value) return true;
    if (projectOptionsError.value) return true;
    if (!projectOptions.value) return false;

    return projectOptionList.value.some(project => project.id === projectFilter.value);
});

const { data, error } = useSWRV(
    () =>
        scopedFetchReady.value
            ? `/models?pageNumber=${pageNumber.value}&pageSize=${pageSize}`
                + `${searchQueryParam.value}${statusQueryParam.value}${projectQueryParam.value}`
            : "",
    getAllModels,
    {
        dedupingInterval: 5_000,
        shouldRetryOnError: false,
        refreshInterval: 5_000
    }
);

useErrorHandler(error);

const scopedProject = computed<IModelProjectOption | null>(() =>
    projectOptionList.value.find(project => project.id === projectFilter.value) ?? null);

// Scoped, but with no name to show for it. A 404 is the one options failure that does NOT land
// here: an API without /models/projects also predates the `project` query param and ignores it,
// so the rows really are estate-wide and the estate header stays truthful. Every other failure
// (5xx, network, timeout) leaves a working scoped endpoint that honours the param — the table
// below IS one project's, and saying "Estate-wide" over it would tell the user the opposite of
// what the page is showing.
const OPTIONS_ENDPOINT_ABSENT = 404;
const scopedWithoutName = computed<boolean>(() => {
    if (!projectFilter.value || !projectOptionsError.value) return false;
    // Duck-typed rather than axios's `isAxiosError`: the value arrives through SWRV as `unknown`,
    // and a failure with no response at all (network/timeout) simply has no status to read.
    const status = (projectOptionsError.value as { response?: { status?: number } }).response?.status;

    return status !== OPTIONS_ENDPOINT_ABSENT;
});

const isScoped = computed<boolean>(() =>
    !!projectFilter.value && (!!scopedProject.value || scopedWithoutName.value));

// What the header and the chip call the scoped project: its name once the options have arrived,
// otherwise the raw id from the URL — the only handle on it when the options call failed.
const scopedProjectLabel = computed<string>(() => scopedProject.value?.name ?? projectFilter.value ?? "");

// The id we have already forced a fresh options fetch for, so a genuinely-absent project is
// judged once against live data instead of looping on mutate().
const revalidatedOptionsFor = ref<string | null>(null);

// A project id we cannot show — a stale bookmark, a deleted project, access since revoked, or a
// typo. The gate above has already kept it off the backend; this drops it from the URL and says
// why, so the page lands on the full list instead of an empty scoped shell.
//
// It cannot judge the id on the options it happens to hold: they are SWRV-cached for a minute
// with no refresh interval, so a project created since the last visit is missing from a list the
// subscription will not refetch inside that window — leaving the gate shut, the fetch key empty
// and the page stranded on the loader until the window lapses. A first miss therefore forces one
// revalidation (`mutate` bypasses the dedup window) and the id is judged on what comes back.
//
// `immediate` is load-bearing: SWRV serves a cached list synchronously during setup, so a plain
// watcher would never see the very value this guard exists to distrust.
watch(
    [projectOptions, projectFilter, projectOptionsValidating],
    ([options, filter, validating]) => {
        if (!filter) {
            revalidatedOptionsFor.value = null;

            return;
        }
        // Nothing can be proved absent while a fetch is in flight, or when one failed — SWRV
        // keeps the last list on error, and bouncing off that would drop a good deep link.
        // scopedWithoutName covers the failure case instead.
        if (validating || projectOptionsError.value) return;
        // Array-checked, not truthiness-checked: an unstubbed route answering with an empty
        // string body must not reach `.some()`.
        if (!Array.isArray(options)) return;
        if (options.some(project => project.id === filter)) return;

        if (revalidatedOptionsFor.value !== filter) {
            revalidatedOptionsFor.value = filter;
            void revalidateProjectOptions();

            return;
        }

        Snackbar.error({
            title: "Project unavailable",
            text: "That project could not be found, so the full models list is shown instead."
        });
        applyProject(null);
    },
    { immediate: true }
);

const models = computed<IModelSummary[]>(() => data.value?.data ?? []);

// Each option's count sums the backend's per-status totals, which ignore the active status filter.
const groupOptions = computed(() => {
    const counts = data.value?.statusCounts ?? {};

    return MODEL_STATUS_GROUPS.map(group => ({
        ...group,
        count: group.statuses.reduce((sum, s) => sum + (counts[s] ?? 0), 0)
    }));
});
const statusOptions = computed<{ key: StatusFilter; label: string; count: number }[]>(() => [{
    key: "all",
    label: "All",
    count: groupOptions.value.reduce((sum, group) => sum + group.count, 0)
}, ...groupOptions.value]);

const TILE_IDLE_CLASS = "bg-white border-gray-200 hover:border-gray-300 "
    + "dark:bg-dark-surface dark:border-dark-border dark:hover:border-dark-border-strong";
// A selected tile is filled as well as ringed, so it never reads as a focus ring. Whole literal
// Tailwind classes so the JIT compiler emits them.
const TILE_SELECTED_CLASS: Record<ModelStatusGroupKey, string> = {
    preparing: "border-amber-500 ring-amber-500/40 bg-amber-50 dark:bg-amber-900/30",
    running: "border-fuchsia-500 ring-fuchsia-500/40 bg-fuchsia-50 dark:bg-fuchsia-900/30",
    queued: "border-gray-500 ring-gray-400/40 bg-gray-100 dark:bg-dark-raised",
    attention: "border-red-500 ring-red-500/40 bg-red-50 dark:bg-red-900/30",
    stopped: "border-gray-500 ring-gray-400/40 bg-gray-100 dark:bg-dark-raised",
    completed: "border-emerald-500 ring-emerald-500/40 bg-emerald-50 dark:bg-emerald-900/30"
};

const EMPTY_COPY: Record<ModelStatusGroupKey, string> = {
    preparing: "No models being prepared.",
    running: "No running models.",
    queued: "No queued models.",
    attention: "No models need attention.",
    stopped: "No stopped models.",
    completed: "No completed models."
};
const emptyMessage = computed<string>(() => {
    if (activeGroup.value) return EMPTY_COPY[activeGroup.value.key];

    return search.value ? "No models match your search." : "No models yet.";
});

type SortKey = "activity" | "name" | "project" | "trusts" | "status";
type SortDir = "asc" | "desc";

interface IColumn {
    key?: SortKey;
    label: string;
    align?: "left" | "right";
}

// Shared grid template so the header and data rows stay column-aligned. Every track is a
// fractional unit (no `auto`): each row is its own grid, so a content-sized column — e.g. a
// variable-width Status pill — would resolve to a different width per row and knock the columns
// out of alignment. Fixed fractions keep every row's tracks identical.
// Column tracks only — each usage site sets its own display class so the
// data rows can swap the grid for the stacked mobile block below sm.
const GRID_CLASS = "grid-cols-[minmax(0,1.7fr)_minmax(0,1.4fr)_minmax(0,1.5fr)_minmax(0,0.9fr)]";

const columns: IColumn[] = [
    {
        key: "name",
        label: "Model"
    },
    {
        key: "project",
        label: "Project"
    },
    {
        key: "trusts",
        label: "Trusts"
    },
    {
        key: "status",
        label: "Status",
        align: "right"
    }
];

// Default: latest activity first, the hub's own order (no column header of its own). An older hub
// sends no lastActivity, so creation time stands in; with neither, every row ties and the stable
// sort keeps the backend order.
const sortKey = ref<SortKey>("activity");
const sortDir = ref<SortDir>("desc");

// First click on a column sorts ascending; subsequent clicks toggle direction.
const toggleSort = (key: SortKey): void => {
    if (sortKey.value === key) {
        sortDir.value = sortDir.value === "asc" ? "desc" : "asc";
    }
    else {
        sortKey.value = key;
        sortDir.value = "asc";
    }
};

// Lifecycle rank so a Status sort groups models by stage in a sensible order.
const STATUS_RANK: Record<ModelStatus, number> = {
    PENDING: 0,
    INITIATED: 1,
    PREPARED: 2,
    RUNNING: 3,
    RESULTS_UPLOADED: 4,
    RESULTS_UPLOAD_FAILED: 5,
    ERROR: 6,
    STOPPED: 7
};
const statusRank = (s: ModelStatus | undefined): number => (s ? STATUS_RANK[s] ?? 99 : 99);

const activityMs = (model: IModelSummary): number =>
    apiTimestampMs(model.lastActivity ?? model.creationTimestamp) ?? 0;

// "activity" deliberately has no tie-break: equal (or absent) timestamps keep
// the backend order under the stable sort.
const SORT_COMPARATORS: Record<SortKey, (a: IModelSummary, b: IModelSummary) => number> = {
    activity: (a, b) => activityMs(a) - activityMs(b),
    name: (a, b) => a.name.localeCompare(b.name),
    project: (a, b) => a.projectName.localeCompare(b.projectName) || a.name.localeCompare(b.name),
    trusts: (a, b) => a.trusts.length - b.trusts.length || a.name.localeCompare(b.name),
    status: (a, b) => statusRank(a.status) - statusRank(b.status) || a.name.localeCompare(b.name)
};

// Client-side sort of the current page (mirrors the Connection Status trusts table).
const sortedModels = computed<IModelSummary[]>(() => {
    const cmp = SORT_COMPARATORS[sortKey.value];

    return [...models.value].sort(sortDir.value === "asc" ? cmp : (a, b) => cmp(b, a));
});

// The row says the same word as the filter it is counted under.
const statusLabel = (status: ModelStatus): string => modelStatusGroup(status)?.label ?? modelStatusLabel(status);

// The left rail is the pill's dot colour, so the two cannot disagree.
const railClass = statusDotClass;

debouncedWatch(
    search,
    () => updateModelList(1),
    { debounce: 500 }
);

const getSearchQuery = (): void => {
    // Encode the term so characters like & ? # % don't corrupt the query string.
    searchQueryParam.value = search.value ? `&search=${encodeURIComponent(search.value)}` : "";
};

const updateModelList = (pageNumberInt: number): void => {
    pageNumber.value = pageNumberInt;
    getSearchQuery();
};

const since = (verb: string, timestamp?: string | null): string => {
    const ms = apiTimestampMs(timestamp);

    return ms === null ? "" : `${verb} ${relativeAgeLabel((Date.now() - ms) / 1000)}`;
};

// What is happening to the model now; empty when the hub sent nothing to say it with.
const activityLabel = (model: IModelSummary): string => {
    if (model.status === "INITIATED" && model.queuePosition != null && model.queuePosition >= 1) {
        return `Position ${model.queuePosition} in queue`;
    }
    const finished = model.finishedAt ?? model.lastActivity;
    if (model.status === "RESULTS_UPLOADED" && finished) return since("Finished", finished);

    return since("Updated", model.lastActivity ?? model.creationTimestamp);
};

// Owner · activity, leaving out whichever is unknown so no placeholder dash ever shows.
const metaLine = (model: IModelSummary): string =>
    [model.ownerName, activityLabel(model)].filter(Boolean).join(" · ");
</script>
