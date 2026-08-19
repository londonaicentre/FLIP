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
                            class="text-xs font-mono uppercase tracking-widest text-gray-500 dark:text-gray-300"
                            data-test="models-scope-eyebrow"
                        >
                            {{ isScoped ? `Project · ${scopedProject?.name}` : "Estate-wide · every project" }}
                        </p>
                        <h1 class="text-3xl font-semibold font-heading mt-1 text-gray-900 dark:text-gray-100">
                            <span class="text-primary-600 underline decoration-4 decoration-primary-500/60 underline-offset-8 dark:text-white">Models</span>
                            <span class="ml-3 text-gray-400 dark:text-gray-300 font-medium">{{ data.totalRecords ?? data.data.length }}</span>
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

                <!-- Filter tiles: one per lifecycle group, each a toggle. Click the active tile to reset. -->
                <div class="flex flex-wrap gap-3 px-8 pb-4">
                    <button
                        v-for="tile in TILES"
                        :key="tile.key"
                        type="button"
                        :data-test="`filter-tile-${tile.key}`"
                        :aria-pressed="activeTiles.has(tile.key)"
                        class="flex-1 min-w-[150px] rounded-xl border bg-white px-4 py-3 text-left transition dark:bg-dark-surface"
                        :class="activeTiles.has(tile.key)
                            ? [tile.ring, 'ring-[3px]']
                            : 'border-gray-200 hover:border-gray-300 dark:border-dark-border dark:hover:border-dark-border-strong'"
                        @click="toggleTile(tile.key)"
                    >
                        <div class="flex items-center gap-2">
                            <span class="inline-block w-2 h-2 rounded-full" :class="tile.dot" />
                            <span class="text-[11px] font-mono uppercase tracking-widest text-gray-500 dark:text-gray-300">
                                {{ tile.label }}
                            </span>
                        </div>
                        <div
                            :data-test="`filter-tile-count-${tile.key}`"
                            class="mt-1 text-2xl font-heading font-bold text-gray-900 dark:text-gray-100"
                        >
                            {{ tileCount(tile) }}
                        </div>
                    </button>
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

                    <label for="project-filter" class="sr-only">Filter by project</label>
                    <!-- Bound to the URL rather than v-model'd: the address bar owns the scope, so
                         the select reflects it instead of holding a second copy of it. -->
                    <select
                        id="project-filter"
                        data-test="project-filter"
                        class="rounded-md border-gray-200 dark:border-dark-border dark:bg-dark-canvas text-sm py-2 pl-3 pr-8"
                        :value="projectFilter ?? ''"
                        @change="applyProject(($event.target as HTMLSelectElement).value || null)"
                    >
                        <option value="">
                            All projects
                        </option>
                        <option v-for="project in projectOptions ?? []" :key="project.id" :value="project.id">
                            {{ project.name }}
                        </option>
                    </select>

                    <span
                        v-if="isScoped"
                        data-test="project-filter-chip"
                        class="inline-flex items-center gap-2 rounded-full border border-primary-200 bg-primary-100 py-1 pl-3 pr-1 text-xs font-semibold text-primary-600 dark:border-dark-border dark:bg-dark-surface dark:text-primary-200"
                    >
                        Project: {{ scopedProject?.name }}
                        <button
                            type="button"
                            data-test="clear-project-filter"
                            aria-label="Clear project filter"
                            title="Clear project filter"
                            class="inline-flex items-center justify-center w-[18px] h-[18px] rounded-full bg-primary-200 hover:bg-primary-300 dark:bg-dark-raised dark:hover:bg-dark-border"
                            @click="applyProject(null)"
                        >
                            <icon-mdi-close class="w-3 h-3" />
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
                                    class="flex items-center gap-1 text-xs font-mono font-medium uppercase tracking-wider text-gray-500 dark:text-gray-300 select-none"
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
                                        class="font-mono text-sm font-semibold text-gray-900 hover:text-primary-600 dark:text-gray-100 dark:hover:text-primary-300"
                                        @click.stop
                                    >
                                        {{ model.name }}
                                    </router-link>
                                    <p data-test="model-owner-meta" class="mt-1 text-xs text-gray-500 dark:text-gray-300">
                                        {{ ownerLabel(model) }} · {{ relativeCreatedLabel(model.creationTimestamp) }}
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
                                        class="text-xs italic text-gray-400 dark:text-gray-300"
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
                                        {{ modelStatusLabelWithQueue(model.status, model.queuePosition) }}
                                    </span>
                                </div>
                            </div>

                            <!-- Mobile: stacked row (design 5a) — name + status chip, project ·
                                 owner meta, then the green-dot trust chips. -->
                            <div class="flex-1 px-4 py-3 sm:hidden">
                                <div class="flex items-start gap-2.5">
                                    <div class="flex-1 min-w-0">
                                        <!-- Design 6a: the project reads as a capitals eyebrow over the name. -->
                                        <router-link
                                            :to="`/project/${model.projectId}`"
                                            data-test="model-project-eyebrow"
                                            class="block font-mono text-[9.5px] uppercase tracking-wider mb-[3px]
                                            truncate text-gray-400 dark:text-gray-300
                                            hover:text-primary-600 dark:hover:text-primary-300"
                                            @click.stop
                                        >
                                            {{ model.projectName }}
                                        </router-link>
                                        <router-link
                                            :to="`/project/${model.projectId}/model/${model.id}`"
                                            data-test="model-name-mobile"
                                            class="font-mono text-sm font-semibold break-words
                                            text-gray-900 dark:text-gray-100
                                            hover:text-primary-600 dark:hover:text-primary-300"
                                            @click.stop
                                        >
                                            {{ model.name }}
                                        </router-link>
                                        <p
                                            v-if="model.description !== undefined"
                                            data-test="model-description-mobile"
                                            class="mt-1 text-[12.5px] leading-snug break-words line-clamp-2
                                            text-gray-500 dark:text-gray-300"
                                        >
                                            {{ model.description }}
                                            <template v-if="!model.description">
                                                <span class="italic text-gray-400 dark:text-gray-300">No description provided...</span>
                                            </template>
                                        </p>
                                    </div>
                                    <span
                                        data-test="model-status-indicator-mobile"
                                        class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium shrink-0"
                                        :class="statusPillClass(model.status)"
                                    >
                                        <span class="inline-block w-1.5 h-1.5 rounded-full" :class="statusDotClass(model.status)" />
                                        {{ modelStatusLabelWithQueue(model.status, model.queuePosition) }}
                                    </span>
                                </div>
                                <div v-if="model.trusts.length" class="flex flex-wrap items-center gap-1.5 mt-2">
                                    <span
                                        v-for="t in model.trusts"
                                        :key="t.id"
                                        data-test="model-trust-chip-mobile"
                                        :title="t.name"
                                        :class="[TRUST_CHIP_CLASS, TRUST_CHIP_PLAIN_PADDING]"
                                    >
                                        {{ trustChipLabel(t) }}
                                    </span>
                                </div>
                                <p
                                    v-else
                                    data-test="model-trusts-empty-mobile"
                                    class="mt-2 text-xs italic text-gray-400 dark:text-gray-300"
                                >
                                    Trusts assigned when training starts
                                </p>
                            </div>
                        </div>

                        <div
                            v-if="!sortedModels.length"
                            class="px-6 py-12 text-center text-sm text-gray-500 dark:text-gray-300"
                        >
                            There are no models to show
                        </div>
                    </AiCard>

                    <AiPagination
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
import { getAllModels,
    getModelProjectOptions,
    IModelProjectOption,
    IModelSummary,
    isModelStatusError,
    ModelStatus,
    modelStatusDotClass as statusDotClass,
    modelStatusLabelWithQueue,
    modelStatusPillClass as statusPillClass } from "@/services/model-service";
import { useModalsStore } from "@/store/modals";
import { apiTimestampMs, relativeCreatedLabel } from "@/utils/helpers";
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
const statusQueryParam = ref("");

type GroupKey = "running" | "preparing" | "queued" | "completed" | "attention";

interface ITile {
    key: GroupKey;
    label: string;
    statuses: ModelStatus[];
    dot: string;
    ring: string;
}

// Summary filter tiles — one per lifecycle group. `statuses` is the set a tile filters to;
// `dot`/`ring` are whole literal Tailwind classes so the JIT compiler emits them.
const TILES: ITile[] = [
    {
        key: "preparing",
        label: "Preparing",
        // PENDING ("Model Created") counts as preparing: the owner is still assembling the
        // app/files, the model isn't waiting on the FL queue yet.
        statuses: ["PENDING", "PREPARED"],
        dot: "bg-amber-500",
        ring: "border-amber-500 ring-amber-500/40"
    },
    {
        key: "running",
        label: "Running",
        statuses: ["RUNNING"],
        dot: "bg-fuchsia-500",
        ring: "border-fuchsia-500 ring-fuchsia-500/40"
    },
    {
        key: "queued",
        label: "Queued",
        statuses: ["INITIATED"],
        dot: "bg-gray-400",
        ring: "border-gray-400 ring-gray-400/40"
    },
    {
        key: "completed",
        label: "Completed",
        statuses: ["RESULTS_UPLOADED"],
        dot: "bg-emerald-500",
        ring: "border-emerald-500 ring-emerald-500/40"
    },
    {
        key: "attention",
        label: "Needs attention",
        statuses: ["ERROR", "RESULTS_UPLOAD_FAILED", "STOPPED"],
        dot: "bg-red-500",
        ring: "border-red-500 ring-red-500/40"
    }
];

// Tiles are accumulative: each click toggles a group in or out of the selection
// and the list shows the union. An empty selection means no status filter (all
// models). The page opens on the active work: Running + Queued.
const DEFAULT_TILES: GroupKey[] = ["running", "queued"];
const activeTiles = ref<Set<GroupKey>>(new Set(DEFAULT_TILES));

const syncStatusFilter = (): void => {
    const statuses = TILES.filter(t => activeTiles.value.has(t.key)).flatMap(t => t.statuses);
    statusQueryParam.value = statuses.length ? `&status=${statuses.join(",")}` : "";
    pageNumber.value = 1;
};

// Arriving already scoped — via "View All Models" or a bookmark — means "show everything in
// this project", so the estate default of Running + Queued would hide most of what was asked
// for. Seeded before syncStatusFilter() so the very first key is already the right one.
if (projectFilter.value) activeTiles.value = new Set();

// Seed the query param before SWRV builds its first key, so the default
// selection doesn't cost an extra unfiltered fetch.
syncStatusFilter();

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
    activeTiles.value = next ? new Set() : new Set<GroupKey>(DEFAULT_TILES);
    if (!next) {
        // Both, deliberately: the debouncedWatch on `search` fires 500ms later, far too late
        // for the key this reset is about to rebuild.
        search.value = "";
        searchQueryParam.value = "";
    }
    syncStatusFilter();
});

const { data, error } = useSWRV(
    () =>
        `/models?pageNumber=${pageNumber.value}&pageSize=${pageSize}`
        + `${searchQueryParam.value}${statusQueryParam.value}${projectQueryParam.value}`,
    getAllModels,
    {
        dedupingInterval: 5_000,
        shouldRetryOnError: false,
        refreshInterval: 5_000
    }
);

useErrorHandler(error);

// The filter's options, and the only place the scoped project's name and status come from — a
// project with no models yet has no row in the list to read them off. Barely changes, so it
// polls not at all and dedupes for a minute.
const { data: projectOptions } = useSWRV("/models/projects", getModelProjectOptions, {
    dedupingInterval: 60_000,
    shouldRetryOnError: false
});

const scopedProject = computed<IModelProjectOption | null>(() =>
    projectOptions.value?.find(project => project.id === projectFilter.value) ?? null);
const isScoped = computed<boolean>(() => !!projectFilter.value && !!scopedProject.value);

// A project id we cannot show — a stale bookmark, a deleted project, access since revoked, or a
// typo. Dropping it here keeps the page off the backend's 403, which would otherwise strand it
// behind the loader with a global error banner. Waits for the options to arrive first, or it
// would fight every deep link during the initial load.
watch([projectOptions, projectFilter], ([options, filter]) => {
    if (!options || !filter) return;
    if (options.some(project => project.id === filter)) return;

    Snackbar.error({
        title: "Project unavailable",
        text: "That project could not be found, so the full models list is shown instead."
    });
    applyProject(null);
});

const models = computed<IModelSummary[]>(() => data.value?.data ?? []);

// A tile's number sums the per-status counts the backend returns for its group.
const tileCount = (tile: ITile): number =>
    tile.statuses.reduce((sum, s) => sum + (data.value?.statusCounts?.[s] ?? 0), 0);

// Toggle a tile's membership in the accumulative selection.
const toggleTile = (key: GroupKey): void => {
    const next = new Set(activeTiles.value);
    if (next.has(key)) {
        next.delete(key);
    }
    else {
        next.add(key);
    }
    activeTiles.value = next;
    syncStatusFilter();
};

type SortKey = "created" | "name" | "project" | "trusts" | "status";
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

// Default: newest first ("created" desc, no column header of its own — the
// created time sits in the Model column's sub-line). When an older deployed
// API sends no timestamps, every row ties at 0 and the stable sort keeps the
// backend order (also newest-first); in a mixed page timestamp-less rows
// would sink to the bottom.
const sortKey = ref<SortKey>("created");
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

const createdMs = (model: IModelSummary): number => apiTimestampMs(model.creationTimestamp) ?? 0;

// "created" deliberately has no tie-break: equal (or absent) timestamps keep
// the backend order under the stable sort.
const SORT_COMPARATORS: Record<SortKey, (a: IModelSummary, b: IModelSummary) => number> = {
    created: (a, b) => createdMs(a) - createdMs(b),
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

// Status-toned left rail: magenta = running, amber = preparing, emerald =
// completed, red = needs attention, neutral = created/queued. PENDING groups under
// the Preparing tile but keeps the neutral tone — nothing is running yet.
const railClass = (status: ModelStatus | undefined): string => {
    if (isModelStatusError(status)) return "bg-red-500";
    if (status === "RESULTS_UPLOADED") return "bg-emerald-500";
    if (status === "RUNNING") return "bg-fuchsia-500";
    if (status === "PREPARED" || status === "PENDING") return "bg-amber-500";

    return "bg-gray-300 dark:bg-gray-600";
};

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


// Owner display name (UserProfile.name from the backend). Falls back to an
// em-dash when the owner has no profile row — the endpoint carries no email.
const ownerLabel = (model: IModelSummary): string => model.ownerName || "—";

// The created half of the owner sub-line ("owner · created 2h ago") comes from
// relativeCreatedLabel in utils/helpers — shared with the Projects list.

// Status pill/dot classes come from model-service (shared with the mobile
// project-models list), imported under their historical local names.
</script>
