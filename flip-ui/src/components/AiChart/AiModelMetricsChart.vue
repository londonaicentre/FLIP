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
import {
    DataZoomComponent,
    DataZoomComponentOption,
    GridComponent,
    GridComponentOption,
    LegendComponent,
    TitleComponent,
    TitleComponentOption,
    ToolboxComponent,
    ToolboxComponentOption,
    TooltipComponent
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import _ from "underscore";
import { computed, ComputedRef, onMounted, ref, watch } from "vue";

import { IModelMetricData } from "@/services/model-service";
import { useSiteSettings } from "@/store/siteSettingsStore";
import { capatilizeString } from "@/utils/helpers";

interface IAiCohortChartProps {
    data: IModelMetricData
}

const props = defineProps<IAiCohortChartProps>();

const siteSettings = useSiteSettings();

const containerRef = ref<HTMLDivElement | HTMLCanvasElement>();

type ECOption = echarts.ComposeOption<
BarSeriesOption
| LineSeriesOption
| TitleComponentOption
| GridComponentOption
| ToolboxComponentOption
| DataZoomComponentOption
>;

onMounted(() => {

    watch(props, () => {
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

            const chart = echarts.init(containerRef.value);
            const colorPalette = ["#a55eea", "#4b7bec", "#2bcbba", "#fd9644", "#fc5c65", "#4b6584", "#2d98da", "#cc7e63", "#724e58", "#4b565b"];
            const chartTitle = capatilizeString(props.data.yLabel);

            const chartOptions: ComputedRef<ECOption> = computed(() => ({
                color: colorPalette,
                colorBy: "series",
                darkMode: siteSettings.getSettings.darkMode,
                backgroundColor: siteSettings.getSettings.darkMode ? "#111827" : "#F9FAFB",
                title: {
                    text: chartTitle,
                    textStyle: {
                        fontSize: 16,
                        fontWeight: 700,
                        fontFamily: "Inter",
                        color: siteSettings.getSettings.darkMode ? "#ccc": "#282A36"
                    }
                },
                textStyle: {
                    fontFamily: "JetBrainsMono",
                    fontWeight: 700
                },
                dataZoom: [
                { type: "slider", bottom: 30, minSpan: 10 },  // visible bar
                { type: "inside", minSpan: 10 }               // scroll/trackpad zoom
                ],
                calculable: true,
                toolbox: {
                    showTitle: false,
                    feature: {
                        dataView: {
                            show: true,
                            title: "View Data",
                            readOnly: true
                        },
                        magicType: {
                            show: true,
                            type: ["line", "bar"]
                        },
                        restore: { title: "Reset View" }
                    }
                },
                grid: {
                    backgroundColor: siteSettings.getSettings.darkMode ? "#282A36": "#4A5462",
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
                    data: props.data.metrics.map(d => d.seriesLabel),
                    left: "center",
                    textStyle: { color: siteSettings.getSettings.darkMode ? "#ccc": "#282A36" }
                },
                xAxis: {
                    type: "value",
                    minorTick: {
                        show: true,
                        splitNumber: 1,
                        lineStyle: { color: siteSettings.getSettings.darkMode ? "#ccc": "#282A36" }
                    },
                    minInterval: 1,
                    splitNumber: 10, // <— try to show 10 ticks (only)
                    name: "Global Rounds",
                    nameLocation: "middle",
                    nameGap: 40,
                    nameTextStyle: {
                        fontWeight: 700,
                        fontSize: 16,
                        fontFamily: "Inter"
                    },
                    axisLabel: {
                        fontWeight: 700,
                        fontFamily: "Inter",
                        hideOverlap: true // <— auto hides colliding labels
                    },
                    axisTick: {
                        show: true,
                        inside: true,
                        lineStyle: { color: siteSettings.getSettings.darkMode ? "#ccc": "#282A36" }
                    },
                    axisLine: { lineStyle: { color: siteSettings.getSettings.darkMode ? "#ccc": "#282A36" } },
                    minorSplitLine: {
                        show: false,
                        lineStyle: { color: siteSettings.getSettings.darkMode ? "#ccc": "#4A5462" }
                    },
                    splitLine: { lineStyle: { color: siteSettings.getSettings.darkMode ? "#ccc": "#111827" } }
                },
                yAxis: {
                    type: "value",
                    minorTick: {
                        show: true,
                        lineStyle: { color: siteSettings.getSettings.darkMode ? "#ccc": "#282A36" }
                    },
                    axisTick: {
                        show: true,
                        inside: true,
                        lineStyle: { color: siteSettings.getSettings.darkMode ? "#ccc": "#282A36" }
                    },
                    axisLabel: {
                        show: true,
                        fontWeight: 900,
                        fontFamily: "JetBrainsMono"
                    },
                    axisLine: {
                        show: false,
                        lineStyle: {
                            color: siteSettings.getSettings.darkMode ? "#ccc": "#111827",
                            width: 1,
                            type: "solid"
                        }
                    },
                    show: true,
                    splitLine: { lineStyle: { color: siteSettings.getSettings.darkMode ? "#ccc": "#111827" } },
                    minorSplitLine: { show: false }
                },
                series: props.data.metrics.map(element => {
                    const sortedData = _.sortBy(element.data, "xValue");
                    const totalPoints = sortedData.length;
                    const totalDuration = 3000; // total animation time (ms)
                    const perPointDelay = totalDuration / totalPoints;

                    return {
                        name: element.seriesLabel,
                        type: "line",
                        // total animation duration for each point
                        animationDuration: perPointDelay,
                        // staggered start for each point
                        animationDelay: (idx) => idx * perPointDelay,
                        data: sortedData.map(d => [d.xValue, d.yValue])
                    };
                })

            }));

            chart.setOption(chartOptions.value);

            // Update the chart if `props.data` changes.
            setTimeout(() => {
                chart.setOption(chartOptions.value);
            }, 500);

            useResizeObserver(containerRef,
                useDebounceFn(() => {
                    chart.resize();
                    chart.setOption(chartOptions.value, false);
                }, 500)
            );

            watch(siteSettings.getSettings, () => {
                chart.setOption(chartOptions.value);
            });
        }
    }, { immediate: true });
});

</script>
