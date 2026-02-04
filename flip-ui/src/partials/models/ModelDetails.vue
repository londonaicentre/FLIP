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
    <section>
        <AiCard>
            <div>
                <h2 class="p-4 text-lg leading-loose font-semibold font-heading grow">
                    Model Details
                </h2>
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
                                {{ model.modelName }}
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
                                {{ model.modelDescription }}
                                <template v-if="!model.modelDescription">
                                    <span class="italic text-gray-400 dark:text-gray-500">No description provided...</span>
                                </template>
                            </dd>
                        </div>
                    </dl>
                </div>
            </div>
        </AiCard>
    </section>
</template>

<script setup lang="ts">
import AiCard from "@/components/AiCard/AiCard.vue";
import { IModelDashboard } from "@/services/model-service";

interface IModelDetails {
    model: IModelDashboard
}

defineProps<IModelDetails>();
</script>
