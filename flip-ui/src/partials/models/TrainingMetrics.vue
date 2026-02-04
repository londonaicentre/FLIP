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
    <div v-if="!data?.length" class="flex flex-col items-center justify-center h-full gap-8 dark:text-gray-300">
        <icon-carbon-chart-line-data class="w-20 h-20 text-gray-400" />
        <div>Any results generated during training will show here.</div>
    </div>
    <div v-else>
        <div class="h-full gap-4 space-y-2 columns-1">
            <AiCard v-for="chartResults in data" :key="chartResults?.yLabel" class="relative grow h-[500px]">
                <div class="grow h-full w-full p-4">
                    <AiMetricsChart :data="chartResults" />
                </div>
            </AiCard>
        </div>
    </div>
</template>

<script setup lang="ts">
import useSWRV from "swrv";
import { useRoute } from "vue-router";

import AiCard from "@/components/AiCard/AiCard.vue";
import AiMetricsChart from "@/components/AiChart/AiModelMetricsChart.vue";
import { getModelMetrics } from "@/services/model-service";

interface ITrainingMetricsProps {
    inProgress: boolean;
}

const props = defineProps<ITrainingMetricsProps>();

const params = useRoute().params;

const { data } = useSWRV(
    `/model/${params.modelId}/metrics`,
    getModelMetrics,
    {
        refreshInterval: props.inProgress ? 5_000 : 0,
        dedupingInterval: 5_000,
        shouldRetryOnError: true,
        revalidateOnFocus: false,
        errorRetryCount: 3
    }
);
</script>
