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
<template>
    <nav class="h-full border-collapse divide-y divide-gray-200 rounded-lg dark:divide-gray-700">
        <transition
            enter-active-class="transition duration-700 ease-out"
            enter-from-class="transform opacity-0 -transform-x-100"
            enter-to-class="transform opacity-100 transform-x-0"
            leave-active-class="transition duration-75 ease-out"
            leave-from-class="transform opacity-100"
            leave-to-class="transform opacity-0 -transform-x-100"
        >
            <div v-if="true" class="flex flex-col w-64 h-full 2xl:w-96">
                <div v-if="!complete && logs" class="p-4 bg-white border-b-4 border-double dark:bg-gray-900 border-b-gray-300 dark:border-b-gray-700">
                    <div class="relative flex items-start h-full space-x-1 left-1">
                        <div class="relative w-4 h-4">
                            <div class="relative flex items-center justify-center w-2 h-2">
                                <span
                                    class="absolute inline-flex w-full h-full rounded-full opacity-50 bg-sky-800 animate-ping"
                                />
                                <span class="relative inline-flex w-2 h-2 bg-blue-500 rounded-full" />
                            </div>
                        </div>
                        <div class="flex-1 min-w-0 px-1 -mt-1.5">
                            <div>
                                <div class="text-sm">
                                    <span class="text-base font-semibold font-heading">
                                        System
                                    </span>
                                </div>
                            </div>
                            <div class="mt-2 text-sm text-gray-700 dark:text-gray-400">
                                <p>
                                    Listening for updates...
                                </p>
                            </div>
                        </div>
                    </div>
                </div>
                <div v-if="logs" class="w-64 overflow-y-auto text-sm bg-gray-50 dark:bg-gray-800 2xl:w-96">
                    <div>
                        <nav class="">
                            <div class="flow-root">
                                <ul role="list" class="relative">
                                    <li
                                        v-for="(log, logIdx) in getOrderedLogs(logs, true)"
                                        :id="`log-${logIdx}`"
                                        :key="logIdx"
                                        class="px-4 mt-4"
                                    >
                                        <div class="relative pb-4">
                                            <div
                                                v-if="logIdx !== getOrderedLogs(logs).length - 1"
                                                class="absolute top-5 left-2 -ml-px h-full w-0.5 bg-gray-200 dark:bg-gray-600"
                                                aria-hidden="true"
                                            />
                                            <div class="relative flex items-start space-x-1">
                                                <div class="relative">
                                                    <div
                                                        class="flex items-center justify-center w-4 h-4 rounded-full"
                                                        :class="[
                                                            log.success ? 'bg-green-50 dark:bg-green-800' : 'bg-red-50 dark:bg-red-800']"
                                                    >
                                                        <div
                                                            class="relative flex items-center justify-center w-2 h-2 rounded-full"
                                                            :class="[
                                                                log.success ? 'bg-green-500' : 'bg-red-600']"
                                                        />
                                                    </div>
                                                </div>
                                                <div class="flex-1 min-w-0 px-1 -mt-1.5">
                                                    <div>
                                                        <div class="text-sm">
                                                            <span class="text-base font-semibold font-heading">{{
                                                                log.trustName ?? "Server"
                                                            }}</span>
                                                        </div>
                                                        <p class="mt-0.5 text-sm text-gray-500 dark:text-gray-300">
                                                            {{ getShortDateFromString(log.logDate) }}
                                                        </p>
                                                    </div>
                                                    <div class="mt-2 text-sm text-gray-700 dark:text-gray-400">
                                                        <p>
                                                            {{ log.log }}
                                                        </p>
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                    </li>
                                </ul>
                            </div>
                        </nav>
                    </div>
                </div>
                <div v-if="!logs && isValidating" class="h-full w-96">
                    <AiLoader />
                </div>
            </div>
        </transition>
    </nav>
</template>

<script lang="ts" setup>
import useSWRV from "swrv";
import { useRoute } from "vue-router";

import AiLoader from "@/components/AiLoader/AiLoader.vue";
import { getLogsForModel } from "@/services/model-service";
import { getOrderedLogs, getShortDateFromString } from "@/utils/helpers";

interface ITimelineProps {
    complete: boolean;
}

const props = defineProps<ITimelineProps>();

const params = useRoute().params;

const { data: logs, isValidating } = useSWRV(
    `/model/${params.modelId}/logs`,
    getLogsForModel,
    {
        refreshInterval: props.complete ? 0 : 5_000,
        dedupingInterval: 5_000,
        shouldRetryOnError: true,
        revalidateOnFocus: false,
        errorRetryCount: 3
    }
);
</script>
