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
    <div class="flex-grow h-full">
        <TransitionRoot
            :show="!data?.data"
            appear
            enter="transition-opacity duration-75"
            enter-from="opacity-0"
            enter-to="opacity-100"
            leave="transition-opacity duration-75"
            leave-from="opacity-100"
            leave-to="opacity-0"
        >
            <span class="transition">
                <div class="flex items-start">
                    <AiSkeleton class="h-8 w-80" />
                    <div class="flex-grow" />
                    <AiSkeleton class="w-24 h-8" />

                </div>
                <AiSkeleton class="w-full h-8 mt-2" />
                <AiSkeleton class="w-full h-8" />
                <AiSkeleton class="w-full h-8" />
            </span>
        </TransitionRoot>
        <TransitionRoot
            :show="!!data?.data"
            enter="transition-opacity duration-300 delay-100"
            enter-from="opacity-0"
            enter-to="opacity-100"
            leave="transition-opacity duration-500"
            leave-from="opacity-100"
            leave-to="opacity-0"
            class="h-full overflow-hidden"
        >
            <div class="flex flex-col flex-1 h-full min-w-0">
                <div class="sticky flex items-center pb-4">
                    <div class="relative flex-grow">
                        <AiSearch
                            v-model="search"
                            placeholder="Search Models"
                            class="w-80"
                            data-test="model-search"
                        />
                    </div>
                    <div v-if="!isViewer">
                        <AiButton
                            light
                            data-test="add-model-btn"
                            class="flex-shrink mr-2"
                            aria-label="Create Model"
                            tooltip="Create Model"
                            @click="addModel"
                        >
                            <icon-mdi-plus class="lg:mr-2" />
                            <span class="hidden lg:inline">Create Model</span>
                        </AiButton>
                    </div>
                </div>
                <div class="h-full overflow-y-auto">
                    <div class="hidden sm:block">
                        <VTable
                            :data="data?.data"
                            data-test="model-list-table"
                            class="table-auto md:table-fixed"
                        >
                            <template #head>
                                <tr class="text-left">
                                    <th class="w-[340px]">
                                        Name
                                    </th>
                                    <th>
                                        Description
                                    </th>
                                    <th class="w-[220px] whitespace-nowrap">
                                        Status
                                    </th>
                                </tr>
                            </template>
                            <template #body="{ rows }">
                                <tr
                                    v-for="row, index in rows"
                                    :key="row.id"
                                    :data-test="`model-list-item-${index}`"
                                    class="cursor-pointer transition hover:bg-gray-50 dark:hover:bg-dark-canvas h-16 [&>td]:py-4"
                                    @click="viewModel(row.id)"
                                >
                                    <td class="font-bold min-w-[300px]">
                                        <router-link
                                            class="break-words line-clamp-2"
                                            :to="`/project/${route.params['projectId']}/model/${row.id}`"
                                            @click.stop
                                        >
                                            {{ row.name }}
                                        </router-link>
                                    </td>
                                    <td>
                                        <div class="flex w-full h-full min-w-[200px] max-w-[300px] md:max-w-none">
                                            <p class="text-gray-500 break-words line-clamp-2 dark:text-gray-300">
                                                {{ row.description }}
                                                <template v-if="!row.description">
                                                    <span class="italic text-gray-400 dark:text-gray-300">No description provided...</span>
                                                </template>
                                            </p>
                                        </div>
                                    </td>
                                    <td :data-test="`model-status-${row.id}`">
                                        <div class="flex items-center gap-2 whitespace-nowrap">
                                            <icon-ph-check
                                                v-if="row.status && !isModelStatusError(row.status)"
                                                class="w-4 h-4 text-green-600 dark:text-green-400 shrink-0"
                                                data-test="model-status-icon-tick"
                                                aria-hidden="true"
                                            />
                                            <icon-ph-x
                                                v-else-if="isModelStatusError(row.status)"
                                                class="w-4 h-4 text-red-600 dark:text-red-400 shrink-0"
                                                data-test="model-status-icon-cross"
                                                aria-hidden="true"
                                            />
                                            <span class="text-sm text-gray-700 dark:text-gray-300">
                                                {{ modelStatusLabel(row.status) }}
                                            </span>
                                        </div>
                                    </td>
                                </tr>
                                <tr v-if="!rows.length">
                                    <td colspan="3" class="text-center">
                                        There are no models to show
                                    </td>
                                </tr>
                            </template>
                        </VTable>
                    </div>
                    <!-- Mobile: stacked three-line rows (design handoff 5a) — name + status
                         chip, clamped description, trust chips (once the endpoint sends
                         trusts). The 3-column table squashes badly below sm. -->
                    <div data-test="model-stacked-list" class="sm:hidden divide-y divide-gray-100 dark:divide-dark-border">
                        <div
                            v-for="row, index in data?.data ?? []"
                            :key="row.id"
                            :data-test="`model-list-item-mobile-${index}`"
                            class="px-1 py-3 cursor-pointer transition hover:bg-gray-50 dark:hover:bg-dark-canvas"
                            @click="viewModel(row.id)"
                        >
                            <div class="flex items-start gap-2.5">
                                <router-link
                                    class="flex-1 min-w-0 font-bold text-sm break-words text-primary-600 dark:text-primary-200"
                                    :to="`/project/${route.params['projectId']}/model/${row.id}`"
                                    @click.stop
                                >
                                    {{ row.name }}
                                </router-link>
                                <span
                                    :data-test="`model-status-chip-${row.id}`"
                                    class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium shrink-0"
                                    :class="modelStatusPillClass(row.status)"
                                >
                                    <span class="inline-block w-1.5 h-1.5 rounded-full" :class="modelStatusDotClass(row.status)" />
                                    {{ modelStatusLabel(row.status) }}
                                </span>
                            </div>
                            <p class="mt-1 text-[12.5px] leading-snug break-words line-clamp-2 text-gray-500 dark:text-gray-300">
                                {{ row.description }}
                                <template v-if="!row.description">
                                    <span class="italic text-gray-400 dark:text-gray-300">No description provided...</span>
                                </template>
                            </p>
                            <div v-if="row.trusts" class="flex flex-wrap gap-[5px] mt-[7px]">
                                <span
                                    v-for="trust in row.trusts"
                                    :key="trust.id"
                                    data-test="model-trust-chip"
                                    class="font-mono text-[10px] text-primary-500 bg-primary-100 border border-[#E4D7EA]
                                    rounded px-[7px] py-[2px] dark:bg-primary-500/15 dark:text-primary-200
                                    dark:border-primary-500/30"
                                >
                                    {{ trust.code || trust.name }}
                                </span>
                                <span v-if="!row.trusts.length" class="italic text-[11px] text-gray-400 dark:text-gray-300">
                                    No Trusts assigned yet
                                </span>
                            </div>
                        </div>
                        <div v-if="!(data?.data ?? []).length" class="py-6 text-center">
                            There are no models to show
                        </div>
                    </div>
                </div>
                <AiPagination
                    :total-pages="data?.totalPages ?? 1"
                    :current-page="data?.page ?? 1"
                    @page-update="updateModelList"
                />
            </div>
        </TransitionRoot>
    </div>
</template>

<script setup lang="ts">
import { TransitionRoot } from "@headlessui/vue";
import { debouncedWatch } from "@vueuse/core";
import useSWRV from "swrv";
import { ref } from "vue";
import { useRoute } from "vue-router";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiPagination from "@/components/AiPagination/AiPagination.vue";
import useErrorHandler from "@/composables/useErrorHandler";
import { usePermissions } from "@/composables/usePermissions";
import { routeChange } from "@/router";
import { getModels,
    isModelStatusError,
    modelStatusDotClass,
    modelStatusLabel,
    modelStatusPillClass } from "@/services/model-service";
import { useModalsStore } from "@/store/modals";

interface IModelListProps {
    pageSize?: number;
}

const props = withDefaults(
    defineProps<IModelListProps>(),
    { pageSize: 20 }
);

const search = ref("");
const pageNumber = ref(1);
const searchQueryParam = ref("");

const modalStore = useModalsStore();
const route = useRoute();
const { isViewer } = usePermissions();

debouncedWatch(
    search,
    () => updateModelList(1),
    { debounce: 500 }
);

const { data, error } = useSWRV(
    () => {
        if(!route.params.projectId) {
            return "";
        }

        return `/projects/${route.params.projectId}/models?pageNumber=${pageNumber.value}&pageSize=${props.pageSize}${searchQueryParam.value}`;
    },
    getModels,
    {
        dedupingInterval: 5_000,
        shouldRetryOnError: false
    }
);

useErrorHandler(error);

const viewModel = (modelId: string) => {
    const projectId = route.params.projectId as string;
    routeChange.viewModel(projectId, modelId);
};

const addModel = () => {
    modalStore.toggleCreateModel();
};

const getSearchQuery = () => {
    if (search.value != "") {
        searchQueryParam.value = `&search=${search.value}`;
    }
    else {
        searchQueryParam.value = "";
    }
};

const updateModelList = (pageNumberInt: number) => {
    pageNumber.value = pageNumberInt;
    getSearchQuery();
};
</script>
