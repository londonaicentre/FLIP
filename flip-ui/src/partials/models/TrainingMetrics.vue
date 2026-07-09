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
        class="flex flex-col flex-1 items-center justify-center w-full min-h-[16rem] p-4
        border-2 border-gray-100 dark:border-dark-border rounded-lg"
    >
        <div class="relative block w-full text-center">
            <icon-ph-chart-line class="w-12 h-12 mx-auto text-gray-400 dark:text-gray-600" />
            <div class="mt-2 text-sm text-gray-500 dark:text-gray-300">
                Any metrics sent during the run will show here.
            </div>
        </div>
    </div>
    <div v-else class="flex flex-col flex-1 min-h-0">
        <div class="flex items-center gap-3 shrink-0 pb-3">
            <!-- Picking one plot only means something while one plot is showing. -->
            <div
                v-if="view === 'single'"
                role="tablist"
                aria-label="Training plots"
                class="flex items-center flex-1 min-w-0 gap-1 overflow-x-auto"
            >
                <button
                    v-for="chart in charts"
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
            <p v-else class="flex-1 min-w-0 text-[13px] text-gray-500 dark:text-gray-300">
                {{ charts.length }} {{ charts.length === 1 ? "plot" : "plots" }}
            </p>

            <div class="flex items-center gap-1 shrink-0" role="group" aria-label="Plot layout">
                <button
                    type="button"
                    data-test="metrics-view-single"
                    aria-label="Show one plot at a time"
                    title="Show one plot at a time"
                    :aria-pressed="view === 'single'"
                    :class="viewButtonClass('single')"
                    @click="view = 'single'"
                >
                    <icon-ph-square class="w-4 h-4" />
                </button>
                <button
                    type="button"
                    data-test="metrics-view-grid"
                    aria-label="Show all plots in a grid"
                    title="Show all plots in a grid"
                    :aria-pressed="view === 'grid'"
                    :class="viewButtonClass('grid')"
                    @click="view = 'grid'"
                >
                    <icon-ph-squares-four class="w-4 h-4" />
                </button>
            </div>
        </div>

        <!-- The chart fills whatever height the card gives it rather than locking to
             a 16:9 box. min-h-0 lets it shrink below its content on a short window;
             the card carries the readability floor. -->
        <div v-if="view === 'single'" class="w-full flex-1 min-h-0 pt-4" role="tabpanel">
            <AiMetricsChart v-if="activeChart" :data="activeChart" />
        </div>

        <!-- Every plot at once. The cells keep a readable height and the grid scrolls,
             rather than squeezing a run's worth of metrics into one screen. -->
        <div
            v-else
            data-test="metrics-grid"
            class="grid flex-1 min-h-0 grid-cols-1 gap-4 pt-4 pr-1 overflow-y-auto md:grid-cols-2 2xl:grid-cols-3"
        >
            <figure
                v-for="chart in charts"
                :key="chart.yLabel"
                :data-test="`metrics-grid-cell-${chart.yLabel}`"
                class="flex flex-col p-2 overflow-hidden rounded-lg ring-1 ring-gray-100 dark:ring-white"
            >
                <figcaption class="px-1 pb-1 text-xs font-semibold truncate shrink-0 text-gray-600 dark:text-gray-300">
                    {{ chart.yLabel }}
                </figcaption>
                <!-- The ratio belongs here rather than on the cell: a grid item is
                     stretched to its row, which overrides aspect-ratio, leaving the plot
                     as flat as its row and its canvas spilling over the cell below. The
                     floor keeps it readable on a phone; the ceiling stops a single
                     column filling the screen. -->
                <div
                    :data-test="`metrics-grid-plot-${chart.yLabel}`"
                    class="w-full aspect-video min-h-[13rem] max-h-[24rem]"
                >
                    <AiMetricsChart :data="chart" />
                </div>
            </figure>
        </div>
    </div>
</template>

<script setup lang="ts">
import { useStorage } from "@vueuse/core";
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

// Every chart, with trust names shortened to their codes. Both views draw from
// this, so a legend reads the same whichever layout you are in.
const charts = computed(() => {
    const codes = nameToCode.value;

    return (data.value ?? []).map(chart => ({
        ...chart,
        metrics: chart.metrics.map(s => ({
            ...s,
            seriesLabel: codes.get(s.seriesLabel) ?? s.seriesLabel
        }))
    }));
});

const activeChart = computed(() => charts.value.find(c => c.yLabel === activeChartLabel.value) ?? null);

// A run reports dozens of metrics, so "one at a time" and "all at once" are both
// reasonable defaults; single is the initial one because it is the readable one.
// The choice is remembered because this component is torn down and rebuilt every
// time you step over to Prepare and back, and re-picking the grid each time grates.
type PlotView = "single" | "grid";
const view = useStorage<PlotView>("flip.metrics-plot-view", "single");

function viewButtonClass(option: PlotView): string {
    const base = "inline-flex items-center justify-center w-8 h-8 rounded-lg transition-colors";

    return view.value === option
        ? `${base} bg-primary-500 text-white`
        : `${base} text-gray-500 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-dark-raised`;
}
</script>
