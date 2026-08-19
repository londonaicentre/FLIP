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
    name: Projects
</route>

<template>
    <div class="flex flex-col w-full h-full">
        <transition name="fade" mode="out-in">
            <AiLoader v-if="!data?.data" />
            <div v-else class="flex flex-col flex-1 min-w-0 overflow-y-auto">
                <!-- Page header (design ref: ProjectsStatusSpine + ProjectsB header section) -->
                <header class="px-8 pt-8 pb-4">
                    <p class="text-xs font-mono uppercase tracking-widest text-gray-500 dark:text-gray-300">
                        Workspace · {{ approvedTrustCount }}
                        {{ approvedTrustCount === 1 ? "trust" : "trusts" }} active
                    </p>
                    <div class="flex items-center justify-between gap-4">
                        <h1 class="text-3xl font-semibold font-heading mt-1 text-gray-900 dark:text-gray-100">
                            <span class="text-primary-600 underline decoration-4 decoration-primary-500/60 underline-offset-8 dark:text-white">Projects</span>
                            <span class="ml-3 text-gray-400 dark:text-gray-300 font-medium">{{ data.data.length }}</span>
                        </h1>
                        <AiButton
                            v-if="canCreateProjects"
                            primary
                            large
                            class="shrink-0"
                            data-test="add-project-btn"
                            aria-label="Create project"
                            tooltip="Create project"
                            @click="addProject"
                        >
                            <icon-mdi-plus class="lg:mr-2" />
                            <span class="hidden lg:inline">Create project</span>
                        </AiButton>
                    </div>
                    <p class="mt-2 text-sm text-gray-500 dark:text-gray-300">
                        Below are the projects you can access.
                    </p>
                </header>

                <!-- Toolbar: search + My/All toggle + sort + view toggle -->
                <div class="flex flex-wrap items-center gap-3 px-8 pb-4">
                    <div class="flex-1 min-w-[240px]">
                        <AiSearch
                            v-model="search"
                            placeholder="Search projects, owners, cohorts…"
                            data-test="project-search"
                        />
                    </div>

                    <div
                        role="tablist"
                        class="inline-flex bg-white dark:bg-dark-canvas border border-gray-200 dark:border-dark-border rounded-lg p-1"
                    >
                        <button
                            v-for="opt in accessOptions"
                            :key="opt.id"
                            type="button"
                            role="tab"
                            class="px-3 py-1.5 rounded-md text-sm font-semibold transition-colors"
                            :class="ownerFilter === opt.value
                                ? 'bg-gray-100 dark:bg-dark-surface text-gray-900 dark:text-gray-100'
                                : 'bg-transparent text-gray-500 hover:text-gray-800 dark:text-gray-300 dark:hover:text-gray-200'"
                            :data-test="`access-filter-${opt.id}`"
                            @click="ownerFilter = opt.value"
                        >
                            {{ opt.label }}
                        </button>
                    </div>

                    <select
                        v-model="sortKey"
                        class="rounded-md border-gray-200 dark:border-dark-border dark:bg-dark-canvas text-sm py-2 pl-3 pr-8"
                        data-test="project-sort"
                    >
                        <option value="created">
                            Sort: Created
                        </option>
                        <option value="status">
                            Sort: Status
                        </option>
                        <option value="name">
                            Sort: Name
                        </option>
                        <option value="cohort">
                            Sort: Cohort size
                        </option>
                    </select>

                    <!-- View toggle: list vs grid -->
                    <div
                        role="group"
                        aria-label="View"
                        class="inline-flex bg-white dark:bg-dark-canvas border border-gray-200 dark:border-dark-border rounded-lg p-1"
                    >
                        <button
                            type="button"
                            class="p-1.5 rounded-md transition-colors"
                            :class="viewMode === 'list'
                                ? 'bg-gray-100 dark:bg-dark-surface text-gray-900 dark:text-gray-100'
                                : 'text-gray-500 hover:text-gray-800 dark:text-gray-300 dark:hover:text-gray-200'"
                            :aria-pressed="viewMode === 'list'"
                            data-test="view-mode-list"
                            title="List view"
                            @click="viewMode = 'list'"
                        >
                            <icon-ph-list-bullets class="w-5 h-5" />
                        </button>
                        <button
                            type="button"
                            class="p-1.5 rounded-md transition-colors"
                            :class="viewMode === 'grid'
                                ? 'bg-gray-100 dark:bg-dark-surface text-gray-900 dark:text-gray-100'
                                : 'text-gray-500 hover:text-gray-800 dark:text-gray-300 dark:hover:text-gray-200'"
                            :aria-pressed="viewMode === 'grid'"
                            data-test="view-mode-grid"
                            title="Grid view"
                            @click="viewMode = 'grid'"
                        >
                            <icon-ph-squares-four class="w-5 h-5" />
                        </button>
                    </div>
                </div>

                <!-- Empty state -->
                <div v-if="!sortedProjects.length" class="flex flex-col items-center justify-center px-8 py-16">
                    <icon-ph-archive-duotone class="w-16 h-16 text-primary-500 dark:text-primary-400" />
                    <p class="mt-3 text-gray-500 dark:text-gray-300">
                        There are no projects to show
                    </p>
                </div>

                <!-- LIST VIEW — design ref: ProjectsStatusSpine (variant A3) -->
                <div v-else-if="viewMode === 'list'" class="px-8 pb-8" data-test="projects-list-view">
                    <!-- font-normal on the row/card anchor: main.css bolds every non-nav <a>, so without
                         it every descendant without its own weight class inherits semibold. Set it on the
                         anchor itself so no leaf can be missed; the name, pills and cohort figure carry
                         their own font-semibold. -->
                    <AiCard class="overflow-hidden p-0">
                        <router-link
                            v-for="(project, idx) in sortedProjects"
                            :key="project.id"
                            :to="`/project/${project.id}`"
                            class="flex items-stretch font-normal bg-white dark:bg-dark-canvas hover:bg-gray-50 dark:hover:bg-dark-surface transition-colors"
                            :class="idx > 0 ? 'border-t border-gray-100 dark:border-dark-border' : ''"
                            :data-test="`project-list-item-${idx}`"
                        >
                            <!-- Fat status spine (8px) — replaces the avatar -->
                            <div
                                class="w-2 self-stretch shrink-0 relative"
                                :class="spineBgClass(project.status)"
                                data-test="project-status-spine"
                            >
                                <div class="absolute inset-0 bg-gradient-to-r from-white/25 to-transparent" />
                            </div>

                            <div
                                :class="ROW_GRID_CLASS"
                                class="grid gap-x-3 gap-y-1 md:gap-5 px-5 py-4 items-center flex-1"
                                data-test="project-row-grid"
                            >
                                <div class="min-w-0">
                                    <div class="font-heading font-semibold text-base text-gray-900 dark:text-gray-100 truncate" data-test="project-name">
                                        {{ project.name }}
                                    </div>
                                    <div class="text-xs text-gray-500 dark:text-gray-300 mt-0.5 truncate">
                                        {{ ownerLabel(project) }} · {{ relativeUpdated(project) }}
                                    </div>
                                </div>
                                <!-- Below md the row is two columns: this drops under the name and
                                     its meta line, while the status block spans both rows to its
                                     right. From md the normal column flow takes over. -->
                                <div
                                    class="col-start-1 row-start-2 md:col-start-auto md:row-start-auto
                                    text-[12.5px] md:text-sm text-gray-600 dark:text-gray-300 leading-snug line-clamp-2"
                                    data-test="project-row-description"
                                >
                                    {{ project.description || "—" }}
                                </div>
                                <TrustChips
                                    :project="project"
                                    class="col-start-2 row-start-2 self-start justify-end flex md:hidden
                                    lg:col-start-auto lg:row-start-auto lg:justify-start lg:self-auto lg:flex"
                                />
                                <div
                                    class="col-start-2 row-start-1 self-start md:col-start-auto
                                    md:row-start-auto md:self-auto
                                    flex flex-col items-end gap-1.5"
                                >
                                    <span
                                        class="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold tracking-wide uppercase"
                                        :class="pillClass(project.status)"
                                        data-test="project-status-indicator"
                                    >
                                        {{ statusLabel(project.status) }}
                                    </span>
                                    <span v-if="nextActionFor(project)" class="text-[11px] text-gray-500 dark:text-gray-300">
                                        {{ nextActionFor(project) }}
                                    </span>
                                    <span v-if="cohortSize(project)" class="text-[11px] text-gray-500 dark:text-gray-300 font-mono">
                                        {{ cohortSize(project)?.toLocaleString() }} patients
                                    </span>
                                </div>
                            </div>
                        </router-link>
                    </AiCard>
                </div>

                <!-- GRID VIEW — design ref: ProjectsB -->
                <div
                    v-else
                    class="px-8 pb-8 grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4"
                    data-test="projects-grid-view"
                >
                    <router-link
                        v-for="(project, idx) in sortedProjects"
                        :key="project.id"
                        :to="`/project/${project.id}`"
                        class="relative flex flex-col gap-3 p-5 font-normal bg-white dark:bg-dark-canvas border border-gray-100 dark:border-dark-border rounded-lg shadow-sm hover:shadow-md hover:border-gray-200 dark:hover:border-dark-border transition-all overflow-hidden"
                        :data-test="`project-card-${idx}`"
                    >
                        <!-- Top status stripe (3px) -->
                        <div class="absolute top-0 inset-x-0 h-[3px]" :class="spineBgClass(project.status)" />

                        <div class="flex items-start justify-between gap-2">
                            <span
                                class="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold tracking-wide uppercase"
                                :class="pillClass(project.status)"
                            >
                                {{ statusLabel(project.status) }}
                            </span>
                        </div>

                        <div>
                            <h3 class="font-heading font-semibold text-lg leading-tight text-gray-900 dark:text-gray-100">
                                {{ project.name }}
                            </h3>
                            <p
                                class="mt-1.5 text-sm text-gray-500 dark:text-gray-300 leading-snug line-clamp-2"
                                data-test="project-card-description"
                            >
                                {{ project.description || "No description provided" }}
                            </p>
                        </div>

                        <TrustChips :project="project" class="flex" />

                        <div
                            class="flex items-center gap-2 px-3 py-2 rounded-md bg-gray-50 dark:bg-dark-surface text-xs"
                            data-test="project-cohort"
                        >
                            <icon-ph-users-three-duotone class="w-4 h-4 text-gray-500 dark:text-gray-300 shrink-0" />
                            <template v-if="cohortSize(project)">
                                <span class="font-mono font-semibold text-gray-800 dark:text-gray-200">
                                    {{ cohortSize(project)?.toLocaleString() }}
                                </span>
                                <span class="text-gray-500 dark:text-gray-300">patients in cohort</span>
                            </template>
                            <span v-else class="text-gray-400 dark:text-gray-300 italic">
                                No cohort yet
                            </span>
                        </div>

                        <div class="flex justify-between items-center pt-2 mt-auto border-t border-gray-100 dark:border-dark-border text-xs text-gray-500 dark:text-gray-300">
                            <span class="truncate max-w-[60%]">{{ ownerLabel(project) }}</span>
                            <span>{{ userCountLabel(project) }}</span>
                        </div>
                    </router-link>
                </div>

                <div class="px-8 pb-8 shrink-0">
                    <AiPagination
                        :total-pages="data?.totalPages ?? 1"
                        :current-page="data?.page ?? 1"
                        @page-update="updateProjectList"
                    />
                </div>
            </div>
        </transition>
    </div>
    <CreateProjectModal :open="modalsStore.createProjectOpen" />
</template>

<script setup lang="ts">
import { debouncedWatch } from "@vueuse/core";
import useSWRV from "swrv";
import { computed, ref } from "vue";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiCard from "@/components/AiCard/AiCard.vue";
import AiLoader from "@/components/AiLoader/AiLoader.vue";
import AiPagination from "@/components/AiPagination/AiPagination.vue";
import AiSearch from "@/components/AiSearch/AiSearch.vue";
import useErrorHandler from "@/composables/useErrorHandler";
import { usePermissions } from "@/composables/usePermissions";
import CreateProjectModal from "@/partials/projects/CreateProjectModal.vue";
import TrustChips from "@/partials/projects/TrustChips.vue";
import { getProjects, IProject, ProjectStatus } from "@/services/project-service";
import { useAuthStore } from "@/store/auth";
import { useModalsStore } from "@/store/modals";
import { apiTimestampMs, relativeCreatedLabel } from "@/utils/helpers";

const pageSize = 20;
const authStore = useAuthStore();
const modalsStore = useModalsStore();
const { canCreateProjects } = usePermissions();

const search = ref("");
const pageNumber = ref(1);
const searchQueryParam = ref("");
const ownerQueryParam = ref("");
// `ownerFilter=true` = "My projects" only; false = all accessible.
const ownerFilter = ref(false);
const userId = authStore.user?.userId;

// Every list row is its own grid, so a content-sized (`auto`) track resolves to a
// different width on each row and the description column drifts down the list.
// Content-independent tracks resolve identically on every row, which is what lines
// the columns up — the same approach as the Models table (`GRID_CLASS` in models.vue).
// Each fr track is wrapped in `minmax(0,…)` deliberately: a bare `1.6fr` means
// `minmax(auto,1.6fr)`, whose min-content floor lets a long name or description push
// its own track past its share — don't simplify them back to bare `fr`. The trusts
// column is `hidden lg:flex`, and a `display: none` child occupies no grid cell, so
// the md template deliberately declares three tracks to the lg template's four.
// Keep each class name whole within one string literal: Tailwind's extractor scans
// source text, so a class split across a concatenation is never generated.
const ROW_GRID_CLASS = "grid-cols-[minmax(0,1fr)_8rem] md:grid-cols-[minmax(0,1.6fr)_minmax(0,1.8fr)_10rem] "
    + "lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1.8fr)_15rem_10rem]";

type ViewMode = "list" | "grid";
const viewMode = ref<ViewMode>("list");

type SortKey = "created" | "status" | "name" | "cohort";
const sortKey = ref<SortKey>("created");

const accessOptions = [
    {
        id: "mine",
        label: "My projects",
        value: true
    },
    {
        id: "all",
        label: "All accessible",
        value: false
    }
];

const { data, error } = useSWRV(
    () =>
        `/projects?pageNumber=${pageNumber.value}&pageSize=${pageSize}${searchQueryParam.value}${ownerQueryParam.value}`,
    getProjects,
    {
        dedupingInterval: 5_000,
        shouldRetryOnError: false,
        refreshInterval: 5_000
    }
);

useErrorHandler(error);

debouncedWatch(
    search,
    () => updateProjectList(1),
    { debounce: 500 }
);

debouncedWatch(
    ownerFilter,
    () => {
        ownerQueryParam.value = ownerFilter.value ? `&owner=${userId}` : "";
        pageNumber.value = 1;
    },
    { debounce: 300 }
);

const addProject = () => {
    modalsStore.toggleCreateProject();
};

const getSearchQuery = () => {
    searchQueryParam.value = search.value ? `&search=${search.value}` : "";
};

const updateProjectList = (pageNumberInt: number) => {
    pageNumber.value = pageNumberInt;
    getSearchQuery();
};

// STAGED first (needs attention), then APPROVED (active), then UNSTAGED (drafts).
const STATUS_RANK: Record<ProjectStatus, number> = {
    STAGED: 0,
    APPROVED: 1,
    UNSTAGED: 2
};

const sortedProjects = computed<IProject[]>(() => {
    const rows = data.value?.data ?? [];
    const arr = [...rows];
    if (sortKey.value === "name") {
        arr.sort((a, b) => a.name.localeCompare(b.name));
    } else if (sortKey.value === "cohort") {
        arr.sort((a, b) => (cohortSize(b) ?? 0) - (cohortSize(a) ?? 0));
    } else if (sortKey.value === "status") {
        arr.sort((a, b) => STATUS_RANK[a.status] - STATUS_RANK[b.status]);
    } else {
        // "created" — newest first; rows without a timestamp sink to the bottom.
        arr.sort((a, b) => {
            const ta = apiTimestampMs(a.creationtimestamp) ?? 0;
            const tb = apiTimestampMs(b.creationtimestamp) ?? 0;

            return tb - ta;
        });
    }

    return arr;
});

const approvedTrustCount = computed(() => {
    const ids = new Set<string>();
    for (const p of data.value?.data ?? []) {
        for (const t of p.approvedTrusts ?? []) {
            if (t.approved) ids.add(t.id);
        }
    }

    return ids.size;
});

const STATUS_LABEL: Record<ProjectStatus, string> = {
    APPROVED: "Approved",
    STAGED: "Staged",
    UNSTAGED: "Draft"
};
const statusLabel = (s: ProjectStatus): string => STATUS_LABEL[s];

const PILL_CLASS: Record<ProjectStatus, string> = {
    APPROVED: "bg-emerald-100 text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-100",
    STAGED: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
    UNSTAGED: "bg-gray-200 text-gray-700 dark:bg-dark-raised dark:text-gray-300"
};
const pillClass = (s: ProjectStatus): string => PILL_CLASS[s];

// TrustChips paints a pending trust's dot in this same STAGED amber — kept in step by
// the group-6 Cypress spec, which compares the two computed colours on a staged row.
const SPINE_BG_CLASS: Record<ProjectStatus, string> = {
    APPROVED: "bg-emerald-600",
    STAGED: "bg-amber-500",
    UNSTAGED: "bg-gray-400"
};
const spineBgClass = (s: ProjectStatus): string => SPINE_BG_CLASS[s];

// What the project is waiting on, when the status pill doesn't already say it. STAGED's
// "awaiting approval" is exactly what the STAGED pill means, so it stays silent; a draft's
// missing cohort query is not derivable from "Draft", so that one earns its line.
const NEXT_ACTION: Record<ProjectStatus, string | null> = {
    APPROVED: null,
    STAGED: null,
    UNSTAGED: "Cohort query needed"
};
const nextActionFor = (project: IProject): string | null => NEXT_ACTION[project.status];

const cohortSize = (project: IProject): number | null => {
    const total = project.query?.totalCohort;

    return typeof total === "number" && total > 0 ? total : null;
};

const ownerLabel = (project: IProject): string => {
    // Prefer the proper display name (UserProfile.name surfaced by the
    // backend). Fall back to the email-derived username so projects whose
    // owner has no UserProfile row still get something readable.
    if (project.ownerName) return project.ownerName;
    const email = project.ownerEmail ?? "";
    if (!email) return "—";
    const at = email.indexOf("@");

    return at > 0 ? email.slice(0, at) : email;
};

const userCountLabel = (project: IProject): string => {
    // Backend (list endpoint) populates userCount from ProjectUserAccess,
    // which includes the owner. Detail-only payloads still expose `users`,
    // so fall back to its length so per-project pages don't show 0.
    const count = project.userCount ?? project.users?.length ?? 0;

    return `${count} ${count === 1 ? "user" : "users"}`;
};

// IProject has no `lastUpdated` — fall back to creation timestamp as a
// proxy for the design's "updated 2h ago". Real updated-at tracking is a
// separate backend change. Formatting is shared with the Models list.
const relativeUpdated = (project: IProject): string => relativeCreatedLabel(project.creationtimestamp);
</script>
