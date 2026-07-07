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
    name: Cohort query
</route>

<template>
    <div class="flex flex-col w-full h-full">
        <Transition name="fade">
            <div v-if="true" class="flex flex-col flex-1 min-w-0 overflow-hidden">
                <main class="flex flex-1 overflow-hidden">
                    <div class="flex flex-col flex-1 overflow-y-auto xl:overflow-hidden">
                        <div class="flex flex-1 xl:overflow-hidden">
                            <div class="p-4 mx-auto grow xl:overflow-y-auto">
                                <header class="px-2 pb-4">
                                    <router-link
                                        v-if="project"
                                        :to="`/project/${project.id}`"
                                        class="text-xs font-semibold tracking-wider uppercase font-mono text-gray-500 hover:text-primary-500 dark:text-gray-300 dark:hover:text-primary-300"
                                    >
                                        {{ project.name }}
                                    </router-link>
                                    <div class="flex items-center justify-between gap-4 mt-2">
                                        <h1 class="text-3xl font-semibold font-heading mt-1 text-gray-900 dark:text-gray-100">
                                            <span class="text-primary-600 underline decoration-4 decoration-primary-500/60 underline-offset-8 dark:text-white">Cohort</span>
                                            <span class="ml-2">query</span>
                                        </h1>
                                        <button
                                            v-if="!queryLocked && !isViewer"
                                            type="submit"
                                            form="cohort-query-form"
                                            :disabled="formSubmitting"
                                            data-test="view-cohort-query-results-btn"
                                            class="inline-flex items-center gap-2 px-4 py-2 text-sm font-semibold text-white bg-[#e51170] hover:bg-[#c50f60] focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-[#e51170] dark:focus:ring-offset-dark-canvas disabled:opacity-60 disabled:cursor-not-allowed rounded-lg shadow-sm transition-colors shrink-0"
                                        >
                                            <svg
                                                v-if="formSubmitting"
                                                class="w-4 h-4 text-white animate-spin"
                                                xmlns="http://www.w3.org/2000/svg"
                                                fill="none"
                                                viewBox="0 0 24 24"
                                            >
                                                <circle
                                                    class="opacity-25"
                                                    cx="12"
                                                    cy="12"
                                                    r="10"
                                                    stroke="currentColor"
                                                    stroke-width="4"
                                                />
                                                <path
                                                    class="opacity-75"
                                                    fill="currentColor"
                                                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                                                />
                                            </svg>
                                            <icon-heroicons-solid-play v-else class="w-4 h-4" />
                                            Run on all trusts
                                        </button>
                                    </div>
                                    <p class="max-w-3xl mt-2 text-sm text-gray-600 dark:text-gray-300">
                                        {{ description }}
                                    </p>
                                </header>
                                <AiCard class="w-full space-y-2">
                                    <CohortQuery
                                        @update-project="updateProject"
                                        @submitting-change="formSubmitting = $event"
                                    />
                                </AiCard>
                            </div>
                        </div>
                    </div>
                </main>
            </div>
        </Transition>
    </div>
</template>

<script lang="ts" setup>
import { storeToRefs } from "pinia";
import { computed, ref } from "vue";

import AiCard from "@/components/AiCard/AiCard.vue";
import { usePermissions } from "@/composables/usePermissions";
import CohortQuery from "@/partials/cohort-query/CohortQuery.vue";
import { useProjectStore } from "@/store/project";

const projectStore = useProjectStore();
const { project } = storeToRefs(projectStore);
const { isViewer } = usePermissions();

const formSubmitting = ref(false);
const queryLocked = computed(() => projectStore.project?.status !== "UNSTAGED");

const description = computed(() => queryLocked.value
    ? "This query is now locked and can not be edited as the project has been staged."
    : "Edit the OMOP query on the left. Each participating trust runs it locally and streams back its cohort count — only aggregated data leaves the trust."
);

const emit = defineEmits(["UpdateProject"]);

const updateProject = () => {
    emit("UpdateProject");
};
</script>
