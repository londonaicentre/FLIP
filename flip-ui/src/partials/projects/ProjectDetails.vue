<!--
    Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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
    <AiCard v-if="projectStore.project" class="dark:bg-gray-900">
        <div class="p-4">
            <h2 class="text-lg font-semibold font-heading grow leading-loose">
                Project Details
            </h2>
        </div>
        <div class="px-4 py-3 border-t border-gray-200 dark:border-gray-700 sm:p-0">
            <dl class="sm:divide-y sm:divide-gray-200 dark:sm:divide-gray-700">
                <div class="py-3 sm:px-4 sm:grid sm:grid-cols-3 sm:gap-4">
                    <dt class="text-sm font-medium text-gray-500 dark:text-gray-300">
                        Name
                    </dt>
                    <dd
                        class="mt-1 text-sm text-gray-700 dark:text-gray-400 break-words sm:mt-0 sm:col-span-2 line-clamp-2"
                        data-test="project-name-text"
                    >
                        {{ projectStore.project.name }}
                    </dd>
                </div>
                <div class="py-3  sm:px-4 sm:grid sm:grid-cols-3 sm:gap-4">
                    <dt class="text-sm font-medium text-gray-500 dark:text-gray-300">
                        Description
                    </dt>
                    <dd
                        class="mt-1 text-sm text-gray-700 dark:text-gray-400 break-words sm:mt-0 sm:col-span-2 line-clamp-4"
                        data-test="project-description-text"
                    >
                        {{ projectStore.project.description }}
                        <template v-if="!projectStore.project.description">
                            <span class="italic text-gray-400">No description provided...</span>
                        </template>
                    </dd>
                </div>
                <div class="py-3  sm:px-4 sm:grid sm:grid-cols-3 sm:gap-4">
                    <dt class="text-sm font-medium text-gray-500 dark:text-gray-300">
                        Created
                    </dt>
                    <dd class="mt-1 text-sm text-gray-700 dark:text-gray-400 sm:mt-0 sm:col-span-2 line-clamp-4">
                        <a
                            :href="`mailto:${projectStore.project.ownerEmail}`"
                            class="text-gray-700 dark:text-gray-300 break-words"
                            data-test="project-creator-text"
                        >
                            {{ projectStore.project.ownerEmail }}</a> on <time
                            data-test="project-created-time"
                            datetime="2020-08-25"
                        > {{
                            getShortDateFromString(projectStore.project.creationtimestamp)
                        }}
                        </time>
                    </dd>
                </div>
            </dl>
        </div>
    </AiCard>
</template>

<script setup lang="ts">
import AiCard from "@/components/AiCard/AiCard.vue";
import { useProjectStore } from "@/store/project";
import { getShortDateFromString } from "@/utils/helpers";

const projectStore = useProjectStore();
</script>
