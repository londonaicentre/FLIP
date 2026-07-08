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
                        <p class="text-xs font-mono uppercase tracking-widest text-gray-500 dark:text-gray-300">
                            Estate-wide · every project
                        </p>
                        <h1 class="text-3xl font-semibold font-heading mt-1 text-gray-900 dark:text-gray-100">
                            <span class="text-primary-600 underline decoration-4 decoration-primary-500/60 underline-offset-8 dark:text-white">Models</span>
                            <span class="ml-3 text-gray-400 dark:text-gray-300 font-medium">{{ data.totalRecords ?? data.data.length }}</span>
                        </h1>
                        <p class="mt-2 text-sm text-gray-500 dark:text-gray-300">
                            The federated training queue across every project you can access.
                        </p>
                    </div>
                </header>

                <!-- Filter tiles: one per lifecycle group, each a toggle. Click the active tile to reset. -->
                <div class="flex flex-wrap gap-3 px-8 pb-4">
                    <button
                        v-for="tile in TILES"
                        :key="tile.key"
                        type="button"
                        :data-test="`filter-tile-${tile.key}`"
                        :aria-pressed="activeTile === tile.key"
                        class="flex-1 min-w-[150px] rounded-xl border bg-white px-4 py-3 text-left transition dark:bg-dark-surface"
                        :class="activeTile === tile.key
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

                <!-- Toolbar: search -->
                <div class="flex flex-wrap items-center gap-3 px-8 pb-4">
                    <div class="flex-1 min-w-[240px]">
                        <AiSearch
                            v-model="search"
                            placeholder="Search models or projects…"
                            data-test="model-search"
                        />
                    </div>
                </div>

                <!-- Models table (div/grid layout: transparent rows + status rail, escapes the
                     global `<table>` styling in main.css) -->
                <div class="px-8 pb-8">
                    <div class="overflow-hidden border border-gray-200 rounded-xl dark:border-dark-border">
                        <!-- Header row: sortable columns -->
                        <div class="flex items-stretch bg-gray-50 border-b border-gray-200 dark:bg-dark-surface dark:border-dark-border">
                            <div class="w-[3px] shrink-0" />
                            <div :class="GRID_CLASS" class="flex-1 gap-4 px-6 py-3">
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
                            class="flex items-stretch border-t border-gray-100 transition first:border-t-0 hover:bg-gray-50 dark:border-dark-border dark:hover:bg-dark-raised"
                        >
                            <!-- Status rail -->
                            <div
                                data-test="model-status-rail"
                                class="w-[3px] shrink-0"
                                :class="railClass(model.status)"
                            />
                            <div :class="GRID_CLASS" class="flex-1 items-center gap-4 px-6 py-4">
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
                                    <p class="mt-1 text-xs text-gray-500 dark:text-gray-300">
                                        {{ ownerLabel(model) }}
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
                                            class="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-white px-2.5 py-0.5 text-xs text-gray-700 dark:border-dark-border dark:bg-dark-surface dark:text-gray-200"
                                        >
                                            <span class="inline-block w-1.5 h-1.5 rounded-full bg-emerald-500" />
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
                                        {{ modelStatusLabel(model.status) }}
                                    </span>
                                </div>
                            </div>
                        </div>

                        <div
                            v-if="!sortedModels.length"
                            class="px-6 py-12 text-center text-sm text-gray-500 dark:text-gray-300"
                        >
                            There are no models to show
                        </div>
                    </div>

                    <AiPagination
                        class="mt-4"
                        :total-pages="data?.totalPages ?? 1"
                        :current-page="data?.page ?? 1"
                        @page-update="updateModelList"
                    />
                </div>
            </div>
        </transition>
    </div>
</template>

<script setup lang="ts">
import { debouncedWatch } from "@vueuse/core";
import useSWRV from "swrv";
import { computed, ref } from "vue";

import AiLoader from "@/components/AiLoader/AiLoader.vue";
import AiPagination from "@/components/AiPagination/AiPagination.vue";
import AiSearch from "@/components/AiSearch/AiSearch.vue";
import useErrorHandler from "@/composables/useErrorHandler";
import { getAllModels,
    IModelSummary,
    IModelSummaryTrust,
    isModelStatusError,
    ModelStatus,
    modelStatusDotClass as statusDotClass,
    modelStatusLabel,
    modelStatusPillClass as statusPillClass } from "@/services/model-service";

const pageSize = 20;

const search = ref("");
const pageNumber = ref(1);
const searchQueryParam = ref("");
const statusQueryParam = ref("");

type GroupKey = "training" | "preparing" | "queued" | "completed" | "attention";

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
        key: "training",
        label: "In training",
        statuses: ["TRAINING_STARTED"],
        dot: "bg-fuchsia-500",
        ring: "border-fuchsia-500 ring-fuchsia-500/40"
    },
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

const activeTile = ref<GroupKey | null>(null);

const { data, error } = useSWRV(
    () =>
        `/models?pageNumber=${pageNumber.value}&pageSize=${pageSize}${searchQueryParam.value}${statusQueryParam.value}`,
    getAllModels,
    {
        dedupingInterval: 5_000,
        shouldRetryOnError: false,
        refreshInterval: 5_000
    }
);

useErrorHandler(error);

const models = computed<IModelSummary[]>(() => data.value?.data ?? []);

// A tile's number sums the per-status counts the backend returns for its group.
const tileCount = (tile: ITile): number =>
    tile.statuses.reduce((sum, s) => sum + (data.value?.statusCounts?.[s] ?? 0), 0);

// Toggle a tile: clicking the active tile clears the filter, otherwise filters to its statuses.
const toggleTile = (key: GroupKey): void => {
    activeTile.value = activeTile.value === key ? null : key;
    const tile = TILES.find(t => t.key === activeTile.value);
    statusQueryParam.value = tile ? `&status=${tile.statuses.join(",")}` : "";
    pageNumber.value = 1;
};

type SortKey = "name" | "project" | "trusts" | "status";
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
const GRID_CLASS = "grid grid-cols-[minmax(0,1.7fr)_minmax(0,1.4fr)_minmax(0,1.5fr)_minmax(0,0.9fr)]";

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

// Null sortKey keeps the backend order (newest-first) until a header is clicked.
const sortKey = ref<SortKey | null>(null);
const sortDir = ref<SortDir>("asc");

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
    TRAINING_STARTED: 3,
    RESULTS_UPLOADED: 4,
    RESULTS_UPLOAD_FAILED: 5,
    ERROR: 6,
    STOPPED: 7
};
const statusRank = (s: ModelStatus | undefined): number => (s ? STATUS_RANK[s] ?? 99 : 99);

const SORT_COMPARATORS: Record<SortKey, (a: IModelSummary, b: IModelSummary) => number> = {
    name: (a, b) => a.name.localeCompare(b.name),
    project: (a, b) => a.projectName.localeCompare(b.projectName) || a.name.localeCompare(b.name),
    trusts: (a, b) => a.trusts.length - b.trusts.length || a.name.localeCompare(b.name),
    status: (a, b) => statusRank(a.status) - statusRank(b.status) || a.name.localeCompare(b.name)
};

// Client-side sort of the current page (mirrors the Connection Status trusts table).
const sortedModels = computed<IModelSummary[]>(() => {
    if (!sortKey.value) return models.value;
    const cmp = SORT_COMPARATORS[sortKey.value];

    return [...models.value].sort(sortDir.value === "asc" ? cmp : (a, b) => cmp(b, a));
});

// Status-toned left rail: magenta = live training, amber = preparing, emerald =
// completed, red = needs attention, neutral = created/queued. PENDING groups under
// the Preparing tile but keeps the neutral tone — nothing is running yet.
const railClass = (status: ModelStatus | undefined): string => {
    if (isModelStatusError(status)) return "bg-red-500";
    if (status === "RESULTS_UPLOADED") return "bg-emerald-500";
    if (status === "TRAINING_STARTED") return "bg-fuchsia-500";
    if (status === "PREPARED") return "bg-amber-500";

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

// Prefer the trust's short code (e.g. "GSTT"); otherwise strip common name bloat
// and truncate so chips stay compact — mirrors the Projects page behaviour.
const trustChipLabel = (trust: IModelSummaryTrust): string => {
    if (trust.code) return trust.code;
    if (!trust.name) return "";
    const stripped = trust.name
        .replace(/\bNHS Foundation Trust\b/gi, "")
        .replace(/\bNHS Trust\b/gi, "")
        .replace(/\bTrust\b/gi, "")
        .trim();
    if (stripped.length <= 16) return stripped;

    return stripped.slice(0, 14) + "…";
};

// Owner display name (UserProfile.name from the backend). Falls back to an
// em-dash when the owner has no profile row — the endpoint carries no email.
const ownerLabel = (model: IModelSummary): string => model.ownerName || "—";

// Status pill/dot classes come from model-service (shared with the mobile
// project-models list), imported under their historical local names.
</script>
