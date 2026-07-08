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
    <AiCard class="flex flex-col">
        <div class="p-4 shrink-0">
            <div class="flex items-center justify-between">
                <h2 class="text-lg font-semibold font-heading grow leading-loose">
                    Models
                </h2>
                <div v-if="!isViewer && projectStore.project?.status === 'APPROVED'">
                    <AiButton
                        light
                        data-test="add-model-btn"
                        aria-label="Create Model"
                        tooltip="Create Model"
                        @click="addModel"
                    >
                        <icon-mdi-plus class="lg:mr-2" />
                        <span class="hidden lg:inline">Create Model</span>
                    </AiButton>
                </div>
            </div>
        </div>
        <div
            v-if="projectStore.project?.status === 'APPROVED'"
            data-test="models-approved-status"
            class="flex flex-col flex-1 min-h-0 overflow-y-auto"
        >
            <div v-if="!data" class="py-12">
                <AiLoader />
            </div>
            <div
                v-else-if="!data?.data?.length"
                data-test="models-empty-state"
                class="flex flex-col flex-1 items-center justify-center px-4 py-8 text-center"
            >
                <icon-ph-graph class="w-12 h-12 text-gray-400 dark:text-gray-600" />
                <div class="mt-2 text-sm">
                    No models have been created for this project yet.
                </div>
            </div>
            <ul v-if="data?.data?.length" role="list" class="border-t border-b border-gray-200 divide-y divide-gray-200 dark:border-dark-border dark:divide-dark-border">
                <li v-for="model in data.data" :key="model.id">
                    <router-link
                        v-slot="{ navigate }"
                        custom
                        :to="`/project/${route.params['projectId']}/model/${model.id}`"
                    >
                        <div
                            class="flex transition items-center cursor-pointer hover:bg-gray-50 dark:hover:bg-dark-surface px-4 py-2 gap-2 group"
                            @click="navigate"
                        >
                            <div class="flex flex-col gap-0.5 w-full text-xs min-w-0">
                                <p class="font-semibold leading-tight text-primary-600 dark:text-primary-200 truncate">
                                    {{ model.name }}
                                </p>
                                <p class="leading-tight text-gray-500 dark:text-gray-300 truncate">
                                    {{ model.description }}
                                    <template v-if="!model.description">
                                        <span class="italic text-gray-400 dark:text-gray-300">No description provided...</span>
                                    </template>
                                </p>
                            </div>
                            <div>
                                <icon-ph-caret-right
                                    class="w-4 h-4 text-gray-400 dark:text-gray-300 transition group-hover:translate-x-0.5"
                                    aria-hidden="true"
                                />
                            </div>
                        </div>
                    </router-link>
                </li>
            </ul>
        </div>
        <template v-else>
            <AiAlert
                text="Project approval is required to view or create models"
                variant="info"
                close
                class="m-auto text-base"
                :bordered="false"
                :rounded="false"
            />

            <div class="relative flex flex-col items-center justify-center" data-test="models-unapproved-status">
                <div class="flex w-full p-4 grow">
                    <div class="w-full space-y-2">
                        <AiSkeleton v-for="i in 2" :key="i" class="h-8 animate-none" />
                    </div>
                </div>
                <div>
                    <div class="absolute inset-0 top-0 w-full h-full backdrop-blur-sm" />
                </div>
            </div>
        </template>
        <div
            v-if="projectStore.project?.status === 'APPROVED' && data?.data?.length"
            class="inline-flex justify-end w-full p-4 space-x-2 shrink-0 mt-auto"
        >
            <AiButton light data-test="view-all-models-btn" :link="`/project/${route.params['projectId']}/models`">
                View All Models
            </AiButton>
        </div>
    </AiCard>
</template>

<script setup lang="ts">
import useSWRV from "swrv";
import { useRoute } from "vue-router";

import AiAlert from "@/components/AiAlert/AiAlert.vue";
import AiButton from "@/components/AiButton/AiButton.vue";
import AiLoader from "@/components/AiLoader/AiLoader.vue";
import useErrorHandler from "@/composables/useErrorHandler";
import { usePermissions } from "@/composables/usePermissions";
import { getModels } from "@/services/model-service";
import { useModalsStore } from "@/store/modals";
import { useProjectStore } from "@/store/project";

const modalStore = useModalsStore();
const projectStore = useProjectStore();
const route = useRoute();

const { isViewer } = usePermissions();

const { data, error } = useSWRV(
    () => {
        if (!route.params.projectId) {
            return "";
        }

        return `/projects/${route.params.projectId}/models?pageSize=5`;
    },
    getModels,
    {
        dedupingInterval: 5_000,
        shouldRetryOnError: false
    }
);

useErrorHandler(error);

const addModel = () => {
    modalStore.toggleCreateModel();
};
</script>
