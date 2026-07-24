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
    <div>
        <Transition
            name="fade"
            mode="out-in"
        >
            <AiLoader v-if="!results || submitting" class="py-8" />
            <div v-else-if="results.trustsResults.length">
                <div class="h-full gap-4 space-y-4 columns-1 lg:columns-2">
                    <div
                        v-for="chartResults in results.trustsResults"
                        :key="chartResults.name"
                        data-test="chart-card"
                        class="relative grow aspect-[4/3] max-h-[500px] overflow-hidden bg-white
                        border border-gray-200 rounded-xl dark:bg-dark-canvas dark:border-dark-border"
                    >
                        <div class="h-full p-4">
                            <AiChart :data="chartResults" class="" />
                        </div>
                    </div>
                </div>
            </div>
            <div v-else-if="!results.trustsResults.length" data-test="no-results-message">
                <div class="py-4">
                    <div>
                        <p class="mt-2 text-lg text-gray-500 dark:text-gray-300">
                            No results can be shown as your query did not return enough records.
                            Please expand your search.
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

import AiChart from "@/components/AiChart/AiCohortChart.vue";
import AiLoader from "@/components/AiLoader/AiLoader.vue";
import { getOMOPResults } from "@/services/cohort-query-service";
import { useProjectStore } from "@/store/project";

interface IProps {
    submitting?: boolean;
}

withDefaults(defineProps<IProps>(), { submitting: false });

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

// Drop cached results when the query id changes so a re-run starts from the
// loader state instead of leaving stale plots on screen while SWRV waits for
// the first poll of the new query.
watch(() => projectStore.project?.query?.id, (newId, oldId) => {
    if (newId !== oldId) {
        results.value = undefined;
    }
});
</script>
