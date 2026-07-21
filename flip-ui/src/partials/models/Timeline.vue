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
    <nav class="h-full border-collapse divide-y divide-gray-200 rounded-lg dark:divide-dark-border">
        <div class="flex flex-col w-full h-full">
            <div v-if="logs" class="w-full overflow-y-auto text-sm bg-white dark:bg-dark-canvas">
                <div>
                    <nav class="">
                        <div class="flow-root">
                            <ul role="list" class="relative">
                                <li
                                    v-for="(log, logIdx) in getOrderedLogs(logs, true)"
                                    :id="`log-${logIdx}`"
                                    :key="logIdx"
                                    class="px-4 mt-3"
                                >
                                    <div class="relative pb-3">
                                        <div
                                            v-if="logIdx !== getOrderedLogs(logs).length - 1"
                                            class="absolute top-4 left-2 -ml-px h-full w-0.5 bg-gray-200 dark:bg-dark-raised"
                                            aria-hidden="true"
                                        />
                                        <div class="relative flex items-start gap-2">
                                            <div class="relative shrink-0 mt-0.5">
                                                <div
                                                    class="flex items-center justify-center w-3 h-3 rounded-full"
                                                    :class="[
                                                        log.success ? 'bg-green-50 dark:bg-green-800' : 'bg-red-50 dark:bg-red-800']"
                                                >
                                                    <div
                                                        class="relative flex items-center justify-center w-1.5 h-1.5 rounded-full"
                                                        :class="[
                                                            log.success ? 'bg-green-500' : 'bg-red-600']"
                                                    />
                                                </div>
                                            </div>
                                            <div class="flex-1 min-w-0">
                                                <div class="flex items-baseline justify-between gap-2">
                                                    <span
                                                        class="text-[11px] font-mono font-bold truncate"
                                                        :class="log.trustName
                                                            ? 'text-gray-800 dark:text-gray-200'
                                                            : 'text-primary-600 dark:text-primary-300'"
                                                        :title="log.trustName ?? undefined"
                                                    >
                                                        {{ log.trustCode ?? log.trustName ?? "Hub" }}
                                                    </span>
                                                    <span class="text-[10px] font-mono text-gray-500 dark:text-gray-300 shrink-0" data-test="log-timestamp">
                                                        {{ getShortDateFromString(log.logDate) }}
                                                    </span>
                                                </div>
                                                <p class="mt-1 text-xs text-gray-700 dark:text-gray-300 break-words">
                                                    {{ log.log }}
                                                </p>
                                            </div>
                                        </div>
                                    </div>
                                </li>
                            </ul>
                        </div>
                    </nav>
                </div>
            </div>
            <div v-if="!logs && isValidating" class="h-full w-full">
                <AiLoader />
            </div>
        </div>
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
