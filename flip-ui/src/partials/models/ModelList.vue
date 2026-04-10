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
                    <div>
                        <AiButton
                            light
                            data-test="add-model-btn"
                            class="flex-shrink mr-2"
                            @click="addModel"
                        >
                            Create Model
                        </AiButton>
                    </div>
                </div>
                <div class="h-full overflow-y-auto">
                    <VTable
                        :data="data?.data"
                        data-test="model-list-table"
                        class="table-auto md:table-fixed"
                    >
                        <template #head>
                            <tr class="text-left">
                                <th class="w-[200px]">
                                    Name
                                </th>
                                <th>
                                    Description
                                </th>
                                <th class="w-[150px]" />
                            </tr>
                        </template>
                        <template #body="{ rows }">
                            <tr
                                v-for="row, index in rows"
                                :key="row.id"
                                :data-test="`model-list-item-${index}`"
                            >
                                <td class="font-bold min-w-[150px]">
                                    <router-link
                                        class="break-words line-clamp-2"
                                        :to="`/project/${route.params['projectId']}/model/${row.id}`"
                                    >
                                        {{ row.name }}
                                    </router-link>
                                </td>
                                <td>
                                    <div class="flex w-full h-full min-w-[200px] max-w-[300px] md:max-w-none">
                                        <p class="text-gray-500 break-words line-clamp-2 dark:text-gray-400">
                                            {{ row.description }}
                                            <template v-if="!row.description">
                                                <span class="italic text-gray-400 dark:text-gray-500">No description provided...</span>
                                            </template>
                                        </p>
                                    </div>
                                </td>
                                <td>
                                    <AiButton
                                        text-primary
                                        text
                                        class="float-right "
                                        data-test="view-models-btn"
                                        @click="() => viewModel(row.id)"
                                    >
                                        View Model
                                    </AiButton>
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
import { routeChange } from "@/router";
import { getModels } from "@/services/model-service";
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
