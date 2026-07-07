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

                <!-- Toolbar: search + status filter -->
                <div class="flex flex-wrap items-center gap-3 px-8 pb-4">
                    <div class="flex-1 min-w-[240px]">
                        <AiSearch
                            v-model="search"
                            placeholder="Search models or projects…"
                            data-test="model-search"
                        />
                    </div>
                    <select
                        v-model="statusFilter"
                        data-test="model-status-filter"
                        aria-label="Filter by status"
                        class="h-10 rounded-lg border border-gray-200 bg-white px-3 text-sm text-gray-700 dark:border-dark-raised dark:bg-dark-canvas dark:text-gray-200"
                    >
                        <option value="">
                            All statuses
                        </option>
                        <option v-for="s in STATUS_OPTIONS" :key="s" :value="s">
                            {{ modelStatusLabel(s) }}
                        </option>
                    </select>
                </div>

                <!-- Models table -->
                <div class="px-8 pb-8">
                    <div class="overflow-hidden border border-gray-200 rounded-xl dark:border-dark-raised">
                        <table class="w-full text-left">
                            <thead class="bg-gray-50 dark:bg-dark-raised">
                                <tr class="text-xs font-mono uppercase tracking-wider text-gray-500 dark:text-gray-300">
                                    <th class="px-6 py-3 font-medium">
                                        Model
                                    </th>
                                    <th class="px-6 py-3 font-medium">
                                        Project
                                    </th>
                                    <th class="px-6 py-3 font-medium">
                                        Trusts
                                    </th>
                                    <th class="px-6 py-3 font-medium text-right">
                                        Status
                                    </th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr
                                    v-for="(model, index) in models"
                                    :key="model.id"
                                    :data-test="`models-list-item-${index}`"
                                    class="border-t border-gray-100 transition hover:bg-gray-50 dark:border-dark-raised dark:hover:bg-dark-canvas"
                                >
                                    <!-- Model -->
                                    <td class="px-6 py-4 align-top">
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
                                    </td>
                                    <!-- Project -->
                                    <td class="px-6 py-4 align-top">
                                        <router-link
                                            :to="`/project/${model.projectId}`"
                                            data-test="model-project"
                                            class="text-sm font-semibold text-gray-900 hover:text-primary-600 dark:text-gray-100 dark:hover:text-primary-300"
                                            @click.stop
                                        >
                                            {{ model.projectName }}
                                        </router-link>
                                    </td>
                                    <!-- Trusts -->
                                    <td class="px-6 py-4 align-top">
                                        <div v-if="model.trusts.length" class="flex flex-wrap items-center gap-1.5">
                                            <span
                                                v-for="t in model.trusts"
                                                :key="t.id"
                                                data-test="model-trust-chip"
                                                :title="t.name"
                                                class="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-white px-2.5 py-0.5 text-xs text-gray-700 dark:border-dark-raised dark:bg-dark-canvas dark:text-gray-200"
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
                                    </td>
                                    <!-- Status -->
                                    <td class="px-6 py-4 align-top text-right">
                                        <span
                                            data-test="model-status-indicator"
                                            class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
                                            :class="statusPillClass(model.status)"
                                        >
                                            <span class="inline-block w-1.5 h-1.5 rounded-full" :class="statusDotClass(model.status)" />
                                            {{ modelStatusLabel(model.status) }}
                                        </span>
                                    </td>
                                </tr>
                                <tr v-if="!models.length">
                                    <td colspan="4" class="px-6 py-12 text-center text-sm text-gray-500 dark:text-gray-300">
                                        There are no models to show
                                    </td>
                                </tr>
                            </tbody>
                        </table>
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
    modelStatusLabel } from "@/services/model-service";

const pageSize = 20;

const search = ref("");
const pageNumber = ref(1);
const searchQueryParam = ref("");
const statusFilter = ref<ModelStatus | "">("");
const statusQueryParam = ref("");

// Lifecycle order, matching the model status enum. Drives the status-filter dropdown.
const STATUS_OPTIONS: ModelStatus[] = [
    "PENDING",
    "INITIATED",
    "PREPARED",
    "TRAINING_STARTED",
    "RESULTS_UPLOADED",
    "RESULTS_UPLOAD_FAILED",
    "ERROR",
    "STOPPED"
];

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

debouncedWatch(
    search,
    () => updateModelList(1),
    { debounce: 500 }
);

debouncedWatch(
    statusFilter,
    () => {
        statusQueryParam.value = statusFilter.value ? `&status=${statusFilter.value}` : "";
        pageNumber.value = 1;
    },
    { debounce: 300 }
);

const getSearchQuery = (): void => {
    searchQueryParam.value = search.value ? `&search=${search.value}` : "";
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

const statusPillClass = (status: ModelStatus | undefined): string => {
    if (isModelStatusError(status)) {
        return "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200";
    }
    if (status === "RESULTS_UPLOADED") {
        return "bg-emerald-100 text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-100";
    }
    if (status === "TRAINING_STARTED" || status === "PREPARED") {
        return "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200";
    }

    return "bg-gray-200 text-gray-700 dark:bg-dark-raised dark:text-gray-300";
};

const statusDotClass = (status: ModelStatus | undefined): string => {
    if (isModelStatusError(status)) return "bg-red-500";
    if (status === "RESULTS_UPLOADED") return "bg-emerald-500";
    if (status === "TRAINING_STARTED" || status === "PREPARED") return "bg-amber-500";

    return "bg-gray-400";
};
</script>
