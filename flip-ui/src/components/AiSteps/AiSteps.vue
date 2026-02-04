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
    <nav class="lg:sticky lg:top-0 lg:z-[1] bg-primary-100 dark:bg-primary-500 border-b border-gray-300 dark:border-primary-500" :class="[!isAtTop && 'transition lg:shadow-md']">
        <ol role="list" class="divide-y divide-gray-300 dark:divide-gray-700 rounded-md lg:flex lg:divide-y-0">
            <li v-for="(step, stepIdx) in steps" :key="step.name" class="relative lg:flex-1 lg:flex">
                <div v-if="step.completed" class="flex items-center w-full group">
                    <span class="flex items-center px-6 py-2 text-sm font-medium grow">
                        <span
                            v-tippy="{placement: 'bottom', content: 'Stage Completed'}"
                            class="flex items-center justify-center flex-shrink-0 w-8 h-8 border-2 border-gray-300 dark:border-primary-400 rounded-full"
                        >
                            <icon-ant-design-check-circle-twotone class="w-8 h-8 text-green-600 dark:text-green-400" aria-hidden="true" />
                        </span>
                        <div class="flex flex-col">
                            <router-link
                                v-if="!!step.scrollTo"
                                class="ml-4 text-sm font-semibold uppercase text-primary-600 dark:text-primary-200"
                                :to="`#${step.scrollTo}`"
                            >
                                {{ step.name }}
                            </router-link>
                            <span v-else class="ml-4 text-sm font-semibold uppercase text-primary-600 dark:text-primary-200">
                                {{ step.name }}
                            </span>
                            <span v-if="step.description" class="ml-4 text-sm font-medium text-gray-700 dark:text-primary-300">{{
                                step.description }}</span>
                        </div>
                    </span>
                </div>
                <div v-else-if="step.error" class="flex items-center w-full group">
                    <span class="flex items-center px-6 py-2 text-sm font-medium grow">
                        <span
                            v-tippy="{placement: 'bottom', content: 'Stage Error'}"
                            class="flex items-center justify-center flex-shrink-0 w-8 h-8 border-2 border-gray-300 dark:border-primary-400 rounded-full"
                        >
                            <icon-ant-design-close-circle-twotone class="w-8 h-8 text-red-700 dark:text-red-400" aria-hidden="true" />
                        </span>
                        <div class="flex flex-col">
                            <router-link
                                v-if="!!step.scrollTo"
                                class="ml-4 text-sm font-semibold uppercase text-primary-600 dark:text-primary-300"
                                :to="`#${step.scrollTo}`"
                            >
                                {{ step.name }}
                            </router-link>
                            <span v-else class="ml-4 text-sm font-semibold uppercase text-primary-600 dark:text-primary-300">
                                {{ step.name }}
                            </span>
                            <span v-if="step.description" class="ml-4 text-sm font-medium text-gray-700 dark:text-primary-300">{{
                                step.description }}</span>
                        </div>
                    </span>
                </div>
                <div v-else-if="step.stopped" class="flex items-center w-full group">
                    <span class="flex items-center px-6 py-2 text-sm font-medium grow">
                        <span
                            v-tippy="{placement: 'bottom', content: 'Stage Stopped'}"
                            class="flex items-center justify-center flex-shrink-0 w-8 h-8 border-2 border-gray-300 dark:border-primary-400 rounded-full"
                        >
                            <icon-ant-design-stop-twotone class="w-8 h-8 text-yellow-600 dark:text-yellow-400" aria-hidden="true" />
                        </span>
                        <div class="flex flex-col">
                            <router-link
                                v-if="!!step.scrollTo"
                                class="ml-4 text-sm font-semibold uppercase text-primary-600 dark:text-primary-200"
                                :to="`#${step.scrollTo}`"
                            >
                                {{ step.name }}
                            </router-link>
                            <span v-else class="ml-4 text-sm font-semibold uppercase text-primary-600 dark:text-primary-200">
                                {{ step.name }}
                            </span>
                            <span v-if="step.description" class="ml-4 text-sm font-medium text-gray-700 dark:text-primary-300">{{
                                step.description }}</span>
                        </div>
                    </span>
                </div>
                <div
                    v-else-if="step.inProgress"
                    class="flex items-center px-6 py-2 text-sm font-medium grow"
                    aria-current="step"
                >
                    <span
                        v-tippy="{placement: 'bottom', content: 'Stage In Progress'}"
                        class="relative flex items-center justify-center flex-shrink-0 w-8 h-8 border-2 border-gray-300 dark:border-primary-400 rounded-full"
                    >
                        <icon-eos-icons-bubble-loading />
                    </span>
                    <div class="flex flex-col">
                        <router-link
                            v-if="!!step.scrollTo"
                            class="ml-4 text-sm font-semibold uppercase text-primary-600 dark:text-primary-200"
                            :to="`#${step.scrollTo}`"
                        >
                            {{ step.name }}
                        </router-link>
                        <span v-else class="ml-4 text-sm font-semibold uppercase text-primary-600 dark:text-primary-200">
                            {{ step.name }}
                        </span>
                        <span v-if="step.description" class="ml-4 text-sm font-medium text-gray-700 dark:text-primary-300">{{
                            step.description
                        }}</span>
                    </div>
                </div>
                <div v-else class="flex items-center grow">
                    <div class="flex items-center flex-grow px-6 py-2 text-sm font-medium ">
                        <span
                            class="flex items-center justify-center flex-shrink-0 w-8 h-8 border-2 border-gray-400 dark:border-primary-400 rounded-full"
                        >
                            <span class="text-gray-500 dark:text-gray-300">{{ step.id }}</span>
                        </span>
                        <div class="flex flex-col">
                            <router-link
                                v-if="!!step.scrollTo"
                                class="ml-4 text-sm font-semibold uppercase text-primary-600 dark:text-primary-200"
                                :to="`#${step.scrollTo}`"
                            >
                                {{ step.name }}
                            </router-link>
                            <span v-else class="ml-4 text-sm font-semibold uppercase text-primary-600 dark:text-primary-200">
                                {{ step.name }}
                            </span>
                            <span v-if="step.description" class="ml-4 text-sm font-medium text-gray-700 dark:text-gray-500">{{
                                step.description }}</span>
                        </div>
                    </div>
                </div>
                <template v-if="stepIdx !== 0">
                    <!-- Separator -->
                    <div class="absolute inset-0 top-0 left-0 hidden w-3 lg:block" aria-hidden="true">
                        <svg
                            class="w-full h-full text-gray-300 dark:text-primary-900"
                            viewBox="0 0 12 82"
                            fill="none"
                            preserveAspectRatio="none"
                        >
                            <path
                                d="M0.5 0V31L10.5 41L0.5 51V82"
                                stroke="currentcolor"
                                vector-effect="non-scaling-stroke"
                            />
                        </svg>
                    </div>
                </template>
            </li>
        </ol>
    </nav>
</template>

<script setup lang="ts">
import { useScroll } from "@vueuse/core";
import { onMounted, ref, watch } from "vue";

export interface IStep {
    id: string;
    name: string;
    description?: string;
    scrollTo?: string;
    completed?: boolean;
    inProgress?: boolean;
    error?: boolean;
    stopped?: boolean;
    action?: () => void;
}

interface IStepsProps {
    steps: IStep[]
}

const isAtTop = ref(true);

defineProps<IStepsProps>();

onMounted(() => {
    const { arrivedState } = useScroll(document.getElementById("breadcrumbs")?.nextElementSibling as HTMLElement);

    watch(arrivedState, () => {
        isAtTop.value = arrivedState.top;
    });

});
</script>
