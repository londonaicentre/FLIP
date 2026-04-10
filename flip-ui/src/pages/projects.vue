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
        <AiBreadcrumbs :pages="[]" :current="{name: 'Projects'}" />
        <transition name="fade" mode="out-in">
            <AiLoader v-if="!data?.data" />
            <div v-else class="flex flex-col flex-1 min-w-0 overflow-hidden">
                <main class="flex flex-1 overflow-hidden">
                    <div class="flex flex-col flex-1 overflow-y-auto xl:overflow-hidden">
                        <nav
                            aria-label="Breadcrumb"
                            class="flex justify-end px-4 py-2 border-b border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 xl:hidden"
                        >
                            <div class="flow-root">
                                <PopoverGroup class="flex items-baseline space-x-4">
                                    <Popover as="div" class="relative z-10 inline-block text-left">
                                        <PopoverButton
                                            class="inline-flex items-center justify-center px-2 py-1 space-x-1 bg-white border-2 border-gray-300 rounded dark:bg-gray-800 dark:border-gray-700 group hover:text-gray-300"
                                            data-test="project-filter-popover"
                                        >
                                            <span class="font-bold font-heading">Filters</span>
                                            <icon-mdi-chevron-down />
                                        </PopoverButton>

                                        <transition
                                            enter-active-class="transition duration-300 ease-out"
                                            enter-from-class="transform scale-95 opacity-0"
                                            enter-to-class="transform opacity-100 scale-300"
                                            leave-active-class="transition duration-75 ease-in"
                                            leave-from-class="transform scale-100 opacity-100"
                                            leave-to-class="transform scale-95 opacity-0"
                                        >
                                            <div v-if="true">
                                                <PopoverPanel
                                                    as="div"
                                                    class="absolute right-0 p-4 mt-2 transition origin-top-right bg-white rounded-md shadow-2xl dark:bg-gray-900 dark:shadow-white/10 dark:shadow-lg w-60 ring-1 ring-black dark:ring-white/20 ring-opacity-5 focus:outline-none"
                                                >
                                                    <Filter
                                                        :label="userFilter.label"
                                                        :description="userFilter.description"
                                                        :checked="ownerFilter"
                                                        data-test="project-filter"
                                                        @filter-updated="updatedOwnerFilter"
                                                    />
                                                </PopoverPanel>
                                            </div>
                                        </transition>
                                    </Popover>
                                </PopoverGroup>
                            </div>
                        </nav>

                        <div class="flex flex-1 xl:overflow-hidden">
                            <nav
                                aria-label="Sections"
                                class="flex-shrink-0 hidden p-4 pr-0 w-60 border-blue-gray-200 xl:flex xl:flex-col"
                            >
                                <div class="pb-4 border-b border-gray-200 dark:border-gray-700">
                                    <span class="font-bold">Filter by</span>
                                </div>
                                <aside class="px-2 py-3 sm:px-6 lg:px-0 lg:col-span-3">
                                    <nav class="space-y-1">
                                        <Filter
                                            :label="userFilter.label"
                                            :description="userFilter.description"
                                            :checked="ownerFilter"
                                            data-test="project-filter"
                                            @filter-updated="updatedOwnerFilter"
                                        />
                                    </nav>
                                </aside>
                            </nav>
                            <div class="p-4 mx-auto overflow-hidden grow">
                                <AiCard class="w-full h-full dark:bg-gray-900">
                                    <div class="flex flex-1 h-full min-w-0 overflow-hidden grow">
                                        <div class="flex flex-col flex-1 h-full min-w-0">
                                            <div class="flex items-center p-4 space-x-8">
                                                <div class="relative flex-grow">
                                                    <AiSearch
                                                        v-model="search"
                                                        placeholder="Search Projects"
                                                        data-test="project-search"
                                                    />
                                                </div>
                                                <div class="flex">
                                                    <AiButton
                                                        light
                                                        block
                                                        :data-test="'add-project-btn'"
                                                        class="flex-shrink w-full"
                                                        @click="addProject"
                                                    >
                                                        Create Project
                                                    </AiButton>
                                                </div>
                                            </div>
                                            <div class="h-full overflow-y-auto grow">
                                                <div class="h-full">
                                                    <ul
                                                        role="list"
                                                        class="h-full border-t border-gray-200 divide-y divide-gray-200 dark:divide-gray-700 dark:border-gray-700"
                                                    >
                                                        <li
                                                            v-for="(project, idx) in data.data"
                                                            :key="project.id"
                                                            :data-test="`project-list-item-${idx}`"
                                                        >
                                                            <router-link
                                                                :to="`/project/${project.id}`"
                                                                class="block px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-800 sm:px-6 group"
                                                                :class="[idx === data.data.length - 1 && 'border-b border-gray-200 dark:border-gray-700']"
                                                                data-test="view-project-btn"
                                                            >
                                                                <div class="flex flex-row items-center gap-4">
                                                                    <div
                                                                        class="relative flex flex-col items-center justify-end w-10 h-10 transition bg-white rounded-full dark:bg-gray-900 ring-2 ring-offset-2 dark:ring-offset-gray-900 shrink-0"
                                                                        :class="[
                                                                            project.status === 'APPROVED' && 'ring-green-600/70 dark:ring-green-400',
                                                                            project.status === 'STAGED' && 'ring-primary-600/70 dark:ring-primary-400',
                                                                            project.status === 'UNSTAGED' && 'ring-gray-400/70 dark:ring-gray-600',
                                                                        ]"
                                                                    >
                                                                        <div class="relative flex items-center justify-center w-full text-gray-700 bg-gray-100 border border-gray-300 rounded-full shadow dark:bg-gray-800 dark:text-gray-300 grow dark:border-gray-500">
                                                                            {{ shortProjectName(project.name) }}
                                                                        </div>
                                                                    </div>
                                                                    <div class="grid items-center w-full grid-cols-3 gap-2">
                                                                        <div class="flex flex-col col-span-3 gap-1 sm:col-span-1">
                                                                            <p
                                                                                class="text-xs font-black tracking-tight uppercase text-primary-600/70 dark:text-primary-400 shrink"
                                                                                data-test="project-status-indicator"
                                                                            >
                                                                                {{ project.status }}
                                                                            </p>

                                                                            <p
                                                                                class="text-sm font-bold truncate text-primary-600 dark:text-gray-400 shrink"
                                                                                data-test="project-name"
                                                                            >
                                                                                {{ project.name }}
                                                                            </p>
                                                                        </div>
                                                                        <div
                                                                            class="hidden col-span-2 sm:flex"
                                                                        >
                                                                            <p
                                                                                class="text-sm font-medium text-gray-500 break-words dark:text-gray-400 shrink line-clamp-2"
                                                                            >
                                                                                {{ project.description }}
                                                                                <template v-if="!project.description">
                                                                                    <span class="italic text-gray-400 dark:text-gray-500">No description provided...</span>
                                                                                </template>
                                                                            </p>
                                                                        </div>
                                                                    </div>
                                                                    <div>
                                                                        <icon-heroicons-outline-chevron-right
                                                                            class="w-5 h-5 text-gray-400 transition group-hover:translate-x-1 group-hover:text-gray-500"
                                                                            aria-hidden="true"
                                                                        />
                                                                    </div>
                                                                </div>
                                                            </router-link>
                                                        </li>
                                                        <li v-if="!data.data.length" class="h-full">
                                                            <div
                                                                class="flex flex-col items-center justify-center h-full px-4 py-4"
                                                            >
                                                                <icon-ph-archive-duotone
                                                                    class="w-16 h-16 text-primary-500 dark:text-primary-400"
                                                                />
                                                                There are no projects to show
                                                            </div>
                                                        </li>
                                                    </ul>
                                                </div>
                                            </div>
                                            <div class="shrink-0">
                                                <AiPagination
                                                    :total-pages="data?.totalPages ?? 1"
                                                    :current-page="data?.page ?? 1"
                                                    @page-update="updateProjectList"
                                                />
                                            </div>
                                        </div>
                                    </div>
                                </AiCard>
                            </div>
                        </div>
                    </div>
                </main>
            </div>
        </transition>
    </div>
    <CreateProjectModal :open="modalsStore.createProjectOpen" />
</template>

<script setup lang="ts">
import { Popover,
    PopoverButton,
    PopoverGroup,
    PopoverPanel } from "@headlessui/vue";
import { debouncedWatch } from "@vueuse/core";
import useSWRV from "swrv";
import { ref } from "vue";

import AiBreadcrumbs from "@/components/AiBreadcrumbs/AiBreadcrumbs.vue";
import AiButton from "@/components/AiButton/AiButton.vue";
import AiCard from "@/components/AiCard/AiCard.vue";
import AiLoader from "@/components/AiLoader/AiLoader.vue";
import AiPagination from "@/components/AiPagination/AiPagination.vue";
import AiSearch from "@/components/AiSearch/AiSearch.vue";
import useErrorHandler from "@/composables/useErrorHandler";
import CreateProjectModal from "@/partials/projects/CreateProjectModal.vue";
import Filter from "@/partials/projects/Filter.vue";
import { getProjects } from "@/services/project-service";
import { useAuthStore } from "@/store/auth";
import { useModalsStore } from "@/store/modals";
import { getInitials } from "@/utils/helpers";

const pageSize = 20;
const authStore = useAuthStore();
const modalsStore = useModalsStore();
const search = ref("");
const pageNumber = ref(1);
const searchQueryParam = ref("");
const ownerQueryParam = ref("");
const ownerFilter = ref(false);
const userId = authStore.user?.userId;
const userFilter = {
    label: "My Projects",
    description: "Show only the projects that you have created"
};

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
        if (ownerFilter.value) {
            ownerQueryParam.value = `&owner=${userId}`;
        } else {
            ownerQueryParam.value = "";
        }
    },
    { debounce: 300 }
);

const addProject = () => {
    modalsStore.toggleCreateProject();
};

const getSearchQuery = () => {
    if (search.value != "") {
        searchQueryParam.value = `&search=${search.value}`;
    } else {
        searchQueryParam.value = "";
    }
};

const updateProjectList = (pageNumberInt: number) => {
    pageNumber.value = pageNumberInt;
    getSearchQuery();
};

const shortProjectName = (name: string) => {
    return getInitials(name);
};

const updatedOwnerFilter = (val: boolean) => ownerFilter.value = val;

</script>
