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

﻿<template>
    <AiCard>
        <div class="p-4">
            <div class="flex flex-row items-center">
                <div class="grow">
                    <h2 class="text-lg font-semibold leading-loose font-heading grow">
                        Imaging Project Status
                    </h2>
                </div>
            </div>
        </div>
        <div v-if="canLoad" class="flex-grow text-sm" data-test="project-status-container">
            <AiAlert
                variant="info"
                :rounded="false"
                :bordered="false"
                text="When this project was approved, an imaging project was created on XNAT at each trust.
                You can monitor the status of each project here."
            />
            <Transition name="fade" mode="out-in">
                <div v-if="!data" class="p-4 space-y-2 transition">
                    <AiSkeleton class="w-1/4 h-8" />
                    <AiSkeleton class="w-full h-8 mt-2" />
                    <AiSkeleton class="w-full h-8" />
                    <AiSkeleton class="w-full h-8" />
                </div>

                <div v-else class="space-y-2">
                    <div class="flex flex-row w-full border-t border-gray-200 dark:border-gray-700">
                        <div class="flex flex-col w-full divide-y divide-gray-100 dark:divide-gray-700">
                            <div class="relative flex-grow w-full p-2">
                                <AiSearch
                                    v-model="search"
                                    placeholder="Filter Trusts"
                                    data-test="filter-project-status"
                                />
                            </div>
                            <ul role="list" class="relative z-0 divide-y divide-gray-200 dark:divide-gray-700">
                                <li
                                    v-for="project in sortedData"
                                    :key="project.trustId"
                                    class="relative p-5 hover:bg-gray-50 dark:hover:bg-gray-800"
                                >
                                    <div
                                        class="flex flex-col gap-2 sm:flex-row sm:gap-0"
                                    >
                                        <!-- Repo name and link -->
                                        <div class="grid grid-cols-2 sm:grid-cols-1 min-w-[30%] 4xl:min-w-[25%] gap-2">
                                            <h2 class="col-span-2 font-bold sm:col-span-1" :data-test="`trust-name-${project.trustId}`">
                                                {{ project.trustName }}
                                            </h2>
                                            <div class="flex flex-row gap-2">
                                                <icon-heroicons-solid-check
                                                    v-if="project.projectCreationCompleted"
                                                    class="w-5 h-5 text-green-500"
                                                    :data-test="`project-creation-complete-${project.trustId}`"
                                                />
                                                <icon-heroicons-outline-clock
                                                    v-else
                                                    class="w-5 h-5 text-gray-400"
                                                    :data-test="`project-creation-incomplete-${project.trustId}`"
                                                />
                                                <span
                                                    class="text-sm font-medium text-gray-400 break-words group-hover:text-gray-700"
                                                >
                                                    {{ project.projectCreationCompleted
                                                        ? 'Created'
                                                        : 'Awaiting creation…' }}
                                                </span>
                                            </div>
                                            <div
                                                v-if="project.reimportCount !== undefined && project.reimportCount !== null && project.projectCreationCompleted"
                                                v-tippy="{ content: project.reimportCount >= maxReimportCount ? ReimportCountLimitMessage : 'Reimport attempts', placement: 'top' }"
                                                class="flex items-center gap-2 w-fit"
                                            >
                                                <RefreshIcon
                                                    v-if="project.reimportCount < maxReimportCount"
                                                    class="w-5 h-5 text-green-500"
                                                />
                                                <ExclamationCircleIcon
                                                    v-else
                                                    class="w-5 h-5 text-yellow-500"
                                                />
                                                <span
                                                    class="text-sm font-medium text-gray-400 break-words group-hover:text-gray-700"
                                                    :data-test="`project-reimport-status-${project.trustId}`"
                                                >
                                                    {{ project.reimportCount }} / {{ maxReimportCount }}
                                                </span>
                                            </div>
                                        </div>
                                        <!-- Repo meta info -->
                                        <div v-if="project.importStatus" class="grid grid-cols-2 gap-2 sm:w-full 4xl:w-[50%] text-sm">
                                            <div class="flex flex-col justify-between">
                                                <span>
                                                    Retrieved
                                                </span>
                                                <span class="font-bold text-gray-400" :data-test="`successful-imports-${project.trustId}`">
                                                    {{ project.importStatus?.successful ?? 0 }}
                                                </span>
                                            </div>
                                            <div class="flex flex-col justify-between">
                                                <span>
                                                    Processing
                                                </span>
                                                <span class="font-bold text-gray-400" :data-test="`processing-imports-${project.trustId}`">
                                                    {{ project.importStatus?.processing ?? 0 }}
                                                </span>
                                            </div>
                                            <div class="flex flex-col justify-between">
                                                <span>
                                                    Queued
                                                </span>
                                                <span class="font-bold text-gray-400" :data-test="`queued-imports-${project.trustId}`">
                                                    {{ project.importStatus?.queued ?? 0 }}
                                                </span>
                                            </div>
                                            <div class="flex flex-col justify-between">
                                                <span>
                                                    Failed
                                                </span>
                                                <span class="font-bold text-gray-400" :data-test="`failed-imports-${project.trustId}`">
                                                    {{ (project.importStatus?.queueFailed
                                                        + project.importStatus?.failed) ?? 0 }}
                                                </span>
                                            </div>
                                        </div>
                                        <div class="flex items-center justify-center">
                                            <AiAlert
                                                v-if="!project.importStatus && project.projectCreationCompleted"
                                                :data-test="`import-status-warning-${project.trustId}`"
                                                variant="info"
                                                text="Awaiting study import status from trust."
                                            />
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
                        <div class="flex flex-col w-1/2 p-4 border-l bg-gray-50 dark:bg-gray-800 border-l-gray-300 dark:border-l-gray-600">
                            <div>
                                <p class="font-bold font-heading">
                                    Overview
                                </p>
                            </div>
                            <div class="flex items-center py-4 divide-x divide-gray-100">
                                <div class="flex flex-col h-full">
                                    <p>
                                        Projects created
                                    </p>
                                    <p class="text-sm text-gray-400" data-test="overview-project-creation">
                                        {{ overview?.projectCreationCompleted }}/{{ overview?.projectCreationTotal }}
                                    </p>
                                </div>
                            </div>
                            <div class="flex items-center py-4 divide-x divide-gray-100">
                                <div class="flex flex-col h-full">
                                    <p>
                                        Studies retrieved
                                    </p>
                                    <p class="text-sm text-gray-400" data-test="overview-image-retrieval">
                                        {{ overview?.studyRetrievalTotal }}
                                    </p>
                                </div>
                            </div>
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
import { computed, ref, watch } from "vue";
import { useRoute } from "vue-router";

import AiAlert from "@/components/AiAlert/AiAlert.vue";
import AiSearch from "@/components/AiSearch/AiSearch.vue";
import AiSkeleton from "@/components/AiSkeleton/AiSkeleton.vue";
import useErrorHandler from "@/composables/useErrorHandler";
import { getImagingProjectsStatus, IImagingProjectStatus } from "@/services/project-service";

interface IImagingProjectStatusProps {
    canLoad: boolean;
}

interface IImagingProjectStatusLocal {
    projectCreationCompleted: number;
    projectCreationTotal: number;
    studyRetrievalTotal: number;
}

const props = defineProps<IImagingProjectStatusProps>();
const route = useRoute();

const search = ref("");

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
        shouldRetryOnError: false,
    }
);

useErrorHandler(error);

watch(() => route.params.projectId, () => {
    data.value = undefined;
}, { flush: 'sync' });

const sortedData = computed(() =>
    sortBy(
        data.value?.filter(
            t => t.trustName.toLowerCase().includes(search.value.toLowerCase())
        ) ?? [],
        "trustName"
    )
);

const overview = computed<IImagingProjectStatusLocal>(() => {
    return {
        projectCreationCompleted: data.value?.filter(trust => trust.projectCreationCompleted)?.length ?? 0,
        projectCreationTotal: data.value?.length ?? 0,
        studyRetrievalTotal: data.value?.reduce((previous, current) => {
            return previous += current.importStatus?.successful ?? 0;
        }, 0) ?? 0
    };
});

const ReimportCountLimitMessage = "The max reimport count has been reached. Any failed studies will not be reimported. Please contact an XNAT administrator for assistance.";

const devMode = process.env.NODE_ENV === "development";
const maxReimportCount = devMode ? 5 : window.MAX_REIMPORT_COUNT;
</script>
