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

<!-- Design ref: design_handoff_full/03_training/training.jsx — variant
     "A · Mission control" plot section (lines 188-202). Charts switch as
     tabs rather than stacking vertically so the active chart gets the full
     card height. -->

<template>
    <div
        v-if="!data?.length"
        data-test="metrics-empty-state"
        class="flex flex-col items-center justify-center w-full min-h-[16rem] p-4
        border-2 border-gray-100 dark:border-dark-border rounded-lg"
    >
        <div class="relative block w-full text-center">
            <icon-ph-chart-line class="w-12 h-12 mx-auto text-gray-400 dark:text-gray-600" />
            <div class="mt-2 text-sm text-gray-500 dark:text-gray-300">
                Any metrics generated during training will show here.
            </div>
        </div>
    </div>
    <div v-else class="flex flex-col">
        <div
            role="tablist"
            aria-label="Training plots"
            class="flex items-center gap-1 shrink-0 overflow-x-auto pb-3"
        >
            <button
                v-for="chart in data"
                :key="chart.yLabel"
                type="button"
                role="tab"
                :aria-selected="activeChartLabel === chart.yLabel"
                :data-test="`training-plot-tab-${chart.yLabel}`"
                class="inline-flex items-center px-3 py-1.5 rounded-full text-[13px] font-semibold whitespace-nowrap transition-all"
                :class="activeChartLabel === chart.yLabel
                    ? 'bg-primary-500 text-white shadow-sm'
                    : 'text-gray-500 hover:text-gray-800 dark:text-gray-300 dark:hover:text-gray-200'"
                @click="activeChartLabel = chart.yLabel"
            >
                {{ chart.yLabel }}
            </button>
        </div>
        <div class="w-full aspect-video max-h-[480px] pt-4" role="tabpanel">
            <AiMetricsChart v-if="activeChart" :data="activeChart" />
        </div>
    </div>
</template>

<script setup lang="ts">
import useSWRV from "swrv";
import { computed, ref, watch } from "vue";
import { useRoute } from "vue-router";

import AiMetricsChart from "@/components/AiChart/AiModelMetricsChart.vue";
import { getModelMetrics } from "@/services/model-service";
import { useProjectStore } from "@/store/project";

interface ITrainingMetricsProps {
    inProgress: boolean;
}

const props = defineProps<ITrainingMetricsProps>();

const params = useRoute().params;
const projectStore = useProjectStore();

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

const activeChartLabel = ref<string | null>(null);

// Default to the first chart whenever the data first loads or the active tab
// disappears from the response (e.g. backend dropped a metric).
watch(
    data,
    next => {
        if (!next?.length) {
            activeChartLabel.value = null;

            return;
        }
        if (!activeChartLabel.value || !next.some(c => c.yLabel === activeChartLabel.value)) {
            activeChartLabel.value = next[0].yLabel;
        }
    },
    { immediate: true }
);

// Trust display name → short code, for legend brevity. Defends against the
// backend metrics endpoint falling back to the long trust name when its
// FLKitSlot→Trust mapping misses.
const nameToCode = computed(() => {
    const map = new Map<string, string>();
    for (const t of projectStore.project?.approvedTrusts ?? []) {
        if (t.code) map.set(t.name, t.code);
    }

    return map;
});

const activeChart = computed(() => {
    const chart = data.value?.find(c => c.yLabel === activeChartLabel.value);
    if (!chart) return null;
    const codes = nameToCode.value;

    return {
        ...chart,
        metrics: chart.metrics.map(s => ({
            ...s,
            seriesLabel: codes.get(s.seriesLabel) ?? s.seriesLabel
        }))
    };
});
</script>
