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
    <div class="p-4">
        <Transition
            name="fade"
            mode="out-in"
        >
            <AiLoader v-if="!results" class="py-8" />
            <div v-else-if="results.trustsResults.length">
                <div class="mt-2">
                    <div class="flex items-baseline mb-8">
                        <span class="mr-2 text-2xl font-semibold leading-8 font-heading">
                            Cohort results
                        </span>
                        <span class="pl-2 mr-2 text-sm leading-8 border-l border-gray-200" data-test="total-results">
                            {{ results.recordCount.toLocaleString() }} - Estimated total results
                        </span>
                    </div>
                </div>
                <div class="h-full gap-4 space-y-4 columns-1 2xl:columns-2">
                    <AiCard
                        v-for="chartResults in results.trustsResults"
                        :key="chartResults.name"
                        class="relative grow h-[500px]"
                    >
                        <div class="h-full p-4">
                            <AiChart :data="chartResults" class="" />
                        </div>
                    </AiCard>
                </div>
            </div>
            <div v-else-if="!results.trustsResults.length">
                <div class="py-4">
                    <div>
                        <h1
                            data-test="no-results-message"
                            class="mt-2 text-xl font-extrabold tracking-tight text-gray-700 sm:text-4xl"
                        >
                            No results to show
                        </h1>
                        <p class="mt-2 text-lg text-gray-500">
                            No results can be shown as your query did not reach the minimum cohort size of <span
                                class="font-bold"
                            >5 records</span>, please expand your search.
                        </p>
                    </div>
                </div>
            </div>
        </Transition>
    </div>
</template>

<script lang="ts" setup>
import { whenever } from "@vueuse/core";
import useSWRV from "swrv";
import { ref, watch } from "vue";

import AiCard from "@/components/AiCard/AiCard.vue";
import AiChart from "@/components/AiChart/AiCohortChart.vue";
import AiLoader from "@/components/AiLoader/AiLoader.vue";
import { getOMOPResults } from "@/services/cohort-query-service";
import { useProjectStore } from "@/store/project";

const projectStore = useProjectStore();

const results = ref();
const getResults = ref("true");

const { data } = useSWRV(
    () =>
    {
        if (!projectStore.project?.query?.id) {
            return "";
        }

        return `/cohort/${projectStore.project?.query?.id}`;
    },
    getOMOPResults,
    {
        // If errored (query not created yet), try again in a second.
        errorRetryInterval: 1_000,
        // Poll for new results every 10 seconds.
        refreshInterval: projectStore.project?.status !== "UNSTAGED" ? 0 : 10_000
    });

whenever(data, () => {
    results.value = data.value;
    getResults.value = "";
}, { immediate: true });

watch(projectStore, () => {
    getResults.value = "true";
}, { immediate: true });
</script>
