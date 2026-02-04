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
import _ from "underscore";
import { computed, ComputedRef, onMounted, ref, watch } from "vue";

import { IResults } from "@/services/cohort-query-service";
import { useSiteSettings } from "@/store/siteSettingsStore";
import { capatilizeString } from "@/utils/helpers";

interface IAiCohortChartProps {
    data: IResults;
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
        const colorPalette = ["#a55eea", "#4b7bec", "#2bcbba", "#fd9644", "#fc5c65", "#4b6584", "#2d98da", "#cc7e63", "#724e58", "#4b565b"];

        const chartTitle = capatilizeString(props.data.name);

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
            dataZoom: {
                minSpan: 10,
                bottom: 30
            },
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
                data: props.data.results.map(d => d.trustName),
                left: "center",
                textStyle: { color: siteSettings.getSettings.darkMode ? "#ccc": "#282A36" }
            },
            xAxis: {
                type: "category",
                minorTick: {
                    show: true,
                    lineStyle: { color: siteSettings.getSettings.darkMode ? "#ccc": "#282A36" }
                },
                name: chartTitle,
                nameLocation: "middle",
                nameGap: 40,
                data: xAxisValues,
                nameTextStyle: {
                    fontWeight: 700,
                    fontSize: 16,
                    fontFamily: "Inter"
                },
                axisLabel: {
                    fontWeight: 700,
                    fontFamily: "Inter"
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
            series:
                props.data.results.map(element => {
                    return {
                        name: element.trustName,
                        type: "bar",
                        data: _.sortBy(element.data, "value").map(data => {

                            return [data.value, data.count];
                        }),
                        showBackground: true,
                        itemStyle: { borderRadius: [5, 5, 0, 0] }
                    };
                })
        }));

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
