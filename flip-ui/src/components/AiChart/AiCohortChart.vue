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
    <div ref="containerRef" class="flex w-full h-full">
        <div ref="chart" class="flex flex-grow w-full" />
    </div>
</template>

<script lang="ts" setup>
import { useDebounceFn, useResizeObserver } from "@vueuse/core";
import { BarChart, BarSeriesOption, LineChart, LineSeriesOption } from "echarts/charts";
import { DataZoomComponent,
    DataZoomComponentOption,
    GridComponent,
    GridComponentOption,
    LegendComponent,
    TitleComponent,
    TitleComponentOption,
    ToolboxComponent,
    ToolboxComponentOption,
    TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { computed, ComputedRef, onMounted, ref, watch } from "vue";

import { CHART_SERIES_COLORS, chartChrome, chartToolbox } from "@/components/AiChart/chartTheme";
import { IResults } from "@/services/cohort-query-service";
import { useSiteSettings } from "@/store/siteSettingsStore";
import { useTrustStore } from "@/store/trusts";
import { capatilizeString } from "@/utils/helpers";

interface IAiCohortChartProps {
    data: IResults;
}

const props = defineProps<IAiCohortChartProps>();

const siteSettings = useSiteSettings();
const trustStore = useTrustStore();

// Prefer the trust's short code (e.g. "GSTT") in legend/series labels so the
// chart legend stays compact; fall back to the name from the cohort result if
// the trust store hasn't loaded yet or the trust has no code set.
const trustLabel = (trustId: string, trustName: string): string => {
    const trust = trustStore.getTrusts.find(t => t.id === trustId);

    return trust?.code || trustName;
};

const containerRef = ref<HTMLDivElement | HTMLCanvasElement>();

// The toolbox magicType toggle only flips echarts' internal state; option
// re-pushes (data updates, theme switch, resize) would revert it unless the
// user's choice is replayed into the rebuilt series.
const seriesType = ref<"bar" | "line">("bar");

type ECOption = echarts.ComposeOption<
BarSeriesOption
| LineSeriesOption
| TitleComponentOption
| GridComponentOption
| ToolboxComponentOption
| DataZoomComponentOption
>;

onMounted(() => {

    if (containerRef.value) {

        echarts.use([
            DataZoomComponent,
            TitleComponent,
            GridComponent,
            LegendComponent,
            BarChart,
            CanvasRenderer,
            ToolboxComponent,
            TooltipComponent,
            LineChart
        ]);

        const xAxisData = props.data.results.map(trust => {
            return trust.data.map(dataPoint => {
                return dataPoint.value;
            });
        }).flat();

        const xAxisValues = [...new Set(xAxisData)].sort();

        const chart = echarts.init(containerRef.value);

        chart.on("magictypechanged", (event) => {
            seriesType.value = (event as { currentType: "bar" | "line" }).currentType;
        });

        const chartTitle = capatilizeString(props.data.name);

        const chartOptions: ComputedRef<ECOption> = computed(() => {
            const darkMode = siteSettings.getSettings.darkMode;
            const chrome = chartChrome(darkMode);

            return {
                color: [...CHART_SERIES_COLORS[darkMode ? "dark" : "light"]],
                colorBy: "series",
                darkMode,
                backgroundColor: chrome.background,
                title: {
                    text: chartTitle,
                    textStyle: {
                        fontSize: 16,
                        fontWeight: 700,
                        fontFamily: "Inter",
                        color: chrome.ink
                    }
                },
                textStyle: {
                    fontFamily: "JetBrainsMono",
                    fontWeight: 700
                },
                dataZoom: {
                    minSpan: 10,
                    bottom: 30
                },
                calculable: true,
                toolbox: chartToolbox(darkMode),
                grid: {
                    left: "5%",
                    right: "5%",
                    bottom: "25%",
                    containLabel: true
                },
                tooltip: {
                    trigger: "axis",
                    axisPointer: {
                        type: "cross",
                        snap: true,
                        crossStyle: { color: "#888" }
                    }
                },
                legend: {
                    data: props.data.results.map(d => trustLabel(d.trustId, d.trustName)),
                    left: "center",
                    textStyle: { color: chrome.ink }
                },
                xAxis: {
                    type: "category",
                    minorTick: {
                        show: true,
                        lineStyle: { color: chrome.axisLine }
                    },
                    name: chartTitle,
                    nameLocation: "middle",
                    nameGap: 40,
                    data: xAxisValues,
                    nameTextStyle: {
                        fontWeight: 700,
                        fontSize: 16,
                        fontFamily: "Inter",
                        color: chrome.ink
                    },
                    axisLabel: {
                        fontWeight: 700,
                        fontFamily: "Inter",
                        color: chrome.axisLabel
                    },
                    axisTick: {
                        show: true,
                        inside: true,
                        lineStyle: { color: chrome.axisLine }
                    },
                    axisLine: { lineStyle: { color: chrome.axisLine } },
                    minorSplitLine: {
                        show: false,
                        lineStyle: { color: chrome.gridLine }
                    },
                    splitLine: { lineStyle: { color: chrome.gridLine } }
                },
                yAxis: {
                    type: "value",
                    minorTick: {
                        show: true,
                        lineStyle: { color: chrome.axisLine }
                    },
                    axisTick: {
                        show: true,
                        inside: true,
                        lineStyle: { color: chrome.axisLine }
                    },
                    axisLabel: {
                        show: true,
                        fontWeight: 900,
                        fontFamily: "JetBrainsMono",
                        color: chrome.axisLabel
                    },
                    axisLine: {
                        show: false,
                        lineStyle: {
                            color: chrome.axisLine,
                            width: 1,
                            type: "solid"
                        }
                    },
                    show: true,
                    splitLine: { lineStyle: { color: chrome.gridLine } },
                    minorSplitLine: { show: false }
                },
                series:
                    props.data.results.map(element => {
                        return {
                            name: trustLabel(element.trustId, element.trustName),
                            type: seriesType.value,
                            // Plain code-unit sort — same ordering _.sortBy gave the string categories.
                            data: [...element.data].sort((a, b) => (a.value < b.value ? -1 : a.value > b.value ? 1 : 0))
                                .map(data => {

                                    return [data.value, data.count];
                                }),
                            showBackground: true,
                            itemStyle: { borderRadius: [5, 5, 0, 0] }
                        };
                    })
            };
        });

        chart.setOption(chartOptions.value);

        useResizeObserver(containerRef,
            useDebounceFn(() => {
                chart.resize();
                chart.setOption(chartOptions.value, false);
            }, 1_000)
        );

        watch(siteSettings.getSettings, () => {
            chart.setOption(chartOptions.value);
        });

        watch(() => props.data, () => {
            chart.setOption(chartOptions.value);
        });
    }
});

</script>
