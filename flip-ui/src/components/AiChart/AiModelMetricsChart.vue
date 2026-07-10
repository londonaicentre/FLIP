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

import { CHART_SERIES_COLORS, chartChrome, chartToolbox, seriesStyle } from "@/components/AiChart/chartTheme";
import { IModelMetricData } from "@/services/model-service";
import { useSiteSettings } from "@/store/siteSettingsStore";

interface IAiCohortChartProps {
    data: IModelMetricData
}

const props = defineProps<IAiCohortChartProps>();

const siteSettings = useSiteSettings();

const containerRef = ref<HTMLDivElement | HTMLCanvasElement>();

// The toolbox magicType toggle only flips echarts' internal state; every full
// option re-push (5s training poll, post-mount tick, resize, theme switch)
// would stomp it back to "line" unless the user's choice is replayed here.
const seriesType = ref<"line" | "bar">("line");

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

            chart.on("magictypechanged", (event) => {
                seriesType.value = (event as { currentType: "line" | "bar" }).currentType;
            });

            // Chart-level title intentionally omitted — the only consumer
            // (TrainingMetrics) renders the y-label as the tab name, so an
            // in-chart title would be redundant noise.
            const chartOptions: ComputedRef<ECOption> = computed(() => {
                const darkMode = siteSettings.getSettings.darkMode;
                const chrome = chartChrome(darkMode);

                return {
                    color: [...CHART_SERIES_COLORS[darkMode ? "dark" : "light"]],
                    colorBy: "series",
                    darkMode,
                    backgroundColor: chrome.background,
                    textStyle: {
                        fontFamily: "JetBrainsMono",
                        fontWeight: 700
                    },
                    calculable: true,
                    // No standing dataZoom components (slider bar / scroll hijack) — zoom is
                    // on-demand via the toolbox magnifier below, reset via "Reset View".
                    toolbox: {
                        ...chartToolbox(darkMode),
                        // Pinned inside the plot's top-right corner (the legend floats below).
                        right: 12,
                        top: 8,
                        feature: {
                            ...chartToolbox(darkMode).feature,
                            dataZoom: {
                                show: true,
                                // "none" keeps the drawn box as the exact view instead of
                                // filtering points outside it (which re-clips the lines).
                                filterMode: "none" as const,
                                title: {
                                    zoom: "Zoom",
                                    back: "Undo Zoom"
                                }
                            }
                        }
                    },
                    grid: {
                        backgroundColor: chrome.background,
                        left: "5%",
                        // Full-width plot — the legend overlays the grid instead of
                        // reserving a 160px column on the right.
                        right: 24,
                        top: 24,
                        // The x-axis name (nameGap 40) needs ~64px; the old "25%" also
                        // reserved room for the now-removed slider bar.
                        bottom: 64,
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
                        data: props.data.metrics
                            .map(d => d.seriesLabel)
                            .slice()
                            .sort((a, b) => a.localeCompare(b)),
                        orient: "vertical",
                        // Floats inside the plot, vertically centred on the right edge; the
                        // translucent card backing keeps it legible where lines pass beneath.
                        right: 12,
                        top: "middle",
                        align: "left",
                        itemGap: 10,
                        padding: 8,
                        backgroundColor: darkMode ? "rgba(14, 11, 19, 0.78)" : "rgba(255, 255, 255, 0.82)",
                        borderColor: chrome.gridLine,
                        borderWidth: 1,
                        borderRadius: 6,
                        textStyle: { color: chrome.ink }
                    },
                    xAxis: {
                        type: "value",
                        // No minInterval: x-values are arbitrary floats (FLIP#148); forcing integer
                        // ticks would collapse sub-unit ranges (e.g. 0–1) onto a single tick.
                        splitNumber: 10, // <— try to show 10 ticks (only)
                        name: props.data.xLabel,
                        nameLocation: "middle",
                        nameGap: 40,
                        nameTextStyle: {
                            fontWeight: 700,
                            fontSize: 16,
                            fontFamily: "Inter",
                            color: chrome.ink
                        },
                        axisLabel: {
                            fontWeight: 700,
                            fontFamily: "Inter",
                            hideOverlap: true, // <— auto hides colliding labels
                            color: chrome.axisLabel
                        },
                        axisTick: { show: false },
                        axisLine: { lineStyle: { color: chrome.axisLine } },
                        splitLine: { show: false } // Mission control: no vertical grid lines
                    },
                    yAxis: {
                        type: "value",
                        axisTick: { show: false },
                        axisLabel: {
                            show: true,
                            fontWeight: 900,
                            fontFamily: "JetBrainsMono",
                            color: chrome.axisLabel
                        },
                        axisLine: { show: false },
                        show: true,
                        // Mission control: horizontal grey hairlines only
                        splitLine: { lineStyle: { color: chrome.gridLine } },
                        minorSplitLine: { show: false }
                    },
                    series: props.data.metrics.map((element, seriesIdx) => {
                        const sortedData = [...element.data].sort((a, b) => a.xValue - b.xValue);
                        const totalPoints = sortedData.length;
                        const totalDuration = 3000; // total animation time (ms)
                        // Guard the empty-series case — 3000/0 = Infinity is an invalid echarts duration.
                        const perPointDelay = totalPoints ? totalDuration / totalPoints : 0;
                        const { color: seriesColor, lineType } = seriesStyle(seriesIdx, darkMode);

                        return {
                            name: element.seriesLabel,
                            type: seriesType.value,
                            smooth: true,
                            showSymbol: true,
                            symbol: "circle",
                            symbolSize: 4,
                            itemStyle: { color: seriesColor },
                            lineStyle: {
                                width: 2,
                                type: lineType
                            },
                            // Mission control: filled AUC at 10% opacity
                            areaStyle: {
                                color: seriesColor,
                                opacity: 0.10
                            },
                            // total animation duration for each point
                            animationDuration: perPointDelay,
                            // staggered start for each point
                            animationDelay: (idx) => idx * perPointDelay,
                            data: sortedData.map(d => [d.xValue, d.yValue])
                        };
                    })
                };
            });

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
