/*
 * Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *     http://www.apache.org/licenses/LICENSE-2.0
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { createTestingPinia } from "@pinia/testing";
import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import { nextTick } from "vue";

import AiModelMetricsChart from "@/components/AiChart/AiModelMetricsChart.vue";
import { CHART_SERIES_COLORS, chartToolbox } from "@/components/AiChart/chartTheme";

const setOption = vi.fn();
const resize = vi.fn();
const on = vi.fn();

vi.mock("echarts/core", () => ({
    init: vi.fn(() => ({
        setOption,
        resize,
        on
    })),
    use: vi.fn()
}));

vi.mock("echarts/charts", () => ({
    BarChart: {},
    LineChart: {}
}));

vi.mock("echarts/components", () => ({
    DataZoomComponent: {},
    GridComponent: {},
    LegendComponent: {},
    TitleComponent: {},
    ToolboxComponent: {},
    TooltipComponent: {}
}));

vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

vi.mock("@vueuse/core", () => ({
    useResizeObserver: vi.fn(),
    useDebounceFn: (fn: unknown) => fn
}));

const DATA = {
    yLabel: "Accuracy",
    xLabel: "Global Rounds",
    metrics: [
        {
            seriesLabel: "Trust B",
            data: [
                {
                    xValue: 1,
                    yValue: 0.5
                },
                {
                    xValue: 2,
                    yValue: 0.7
                }
            ]
        },
        {
            seriesLabel: "Trust A",
            data: [{
                xValue: 1,
                yValue: 0.6
            }]
        }
    ]
};

function mountChart() {
    return mount(AiModelMetricsChart, {
        props: { data: DATA },
        global: {
            plugins: [createTestingPinia({
                createSpy: vi.fn,
                stubActions: false
            })]
        }
    });
}

describe("AiModelMetricsChart", () => {
    beforeEach(() => {
        setOption.mockReset();
        resize.mockReset();
        on.mockReset();
        vi.useFakeTimers();
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it("mounts and pushes an option payload to echarts.init", async () => {
        const wrapper = mountChart();
        await nextTick();
        await flushPromises();

        expect(wrapper.exists()).toBe(true);
        expect(setOption).toHaveBeenCalled();
    });

    it("overlays the legend and toolbox inside the plot instead of reserving a side column", async () => {
        mountChart();
        await nextTick();
        await flushPromises();

        const opts = setOption.mock.calls[0][0];
        // The grid takes the full card width — no 160px legend column on the right.
        expect(opts.grid.right).toBeLessThanOrEqual(24);
        // Toolbox pinned to the plot's top-right corner, legend floating just below it,
        // with a translucent backing so it stays legible over the series lines.
        expect(opts.toolbox.right).toBe(12);
        expect(opts.toolbox.top).toBe(8);
        expect(opts.legend.right).toBe(12);
        expect(opts.legend.top).toBe(40);
        expect(opts.legend.backgroundColor).toBeTruthy();
    });

    it("ships no persistent zoom UI, only the on-demand toolbox box-zoom", async () => {
        mountChart();
        await nextTick();
        await flushPromises();

        const opts = setOption.mock.calls[0][0];
        // No dataZoom components: no slider bar under the plot, no scroll/trackpad
        // hijack. Zoom lives behind the toolbox magnifier instead.
        expect(opts.dataZoom).toBeUndefined();
        expect(opts.toolbox.feature.dataZoom.show).toBe(true);
        expect(opts.toolbox.feature.dataZoom.filterMode).toBe("none");
    });

    it("emits a line series per metric and sorts the legend alphabetically", async () => {
        mountChart();
        await nextTick();
        await flushPromises();

        const opts = setOption.mock.calls[0][0];
        expect(opts.series).toHaveLength(2);
        expect(opts.series.every((s: { type: string }) => s.type === "line")).toBe(true);
        // Series preserve input order, legend gets a sorted snapshot.
        expect(opts.series.map((s: { name: string }) => s.name)).toEqual(["Trust B", "Trust A"]);
        expect(opts.legend.data).toEqual(["Trust A", "Trust B"]);
    });

    it("sorts each series' data by xValue", async () => {
        mount(AiModelMetricsChart, {
            props: {
                data: {
                    yLabel: "y",
                    xLabel: "x",
                    metrics: [{
                        seriesLabel: "A",
                        data: [
                            {
                                xValue: 3,
                                yValue: 0.3
                            },
                            {
                                xValue: 1,
                                yValue: 0.1
                            },
                            {
                                xValue: 2,
                                yValue: 0.2
                            }
                        ]
                    }]
                }
            },
            global: {
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })]
            }
        });
        await nextTick();
        await flushPromises();

        const opts = setOption.mock.calls[0][0];
        expect(opts.series[0].data).toEqual([[1, 0.1], [2, 0.2], [3, 0.3]]);
    });

    it("themes chrome and series from the shared chart theme", async () => {
        mountChart();
        await nextTick();
        await flushPromises();

        const opts = setOption.mock.calls[0][0];
        expect(opts.color).toEqual([...CHART_SERIES_COLORS.light]);
        expect(opts.backgroundColor).toBe("transparent");
        expect(opts.grid.backgroundColor).toBe("transparent");
        expect(opts.series[0].itemStyle.color).toBe(CHART_SERIES_COLORS.light[0]);
        // The shared toolbox theme applies as-is; this chart adds its own
        // on-demand box-zoom feature on top (covered by the zoom test above).
        expect(opts.toolbox).toMatchObject(chartToolbox(false));

        // The off-token fills/inks flagged in the dark-mode review must not resurface.
        const flattened = JSON.stringify(opts);
        for (const legacyHex of ["#111827", "#282A36", "#4A5462"]) {
            expect(flattened).not.toContain(legacyHex);
        }
    });

    it("gives a ninth series a dashed line rather than silently repeating a colour", async () => {
        mount(AiModelMetricsChart, {
            props: {
                data: {
                    yLabel: "y",
                    xLabel: "x",
                    metrics: Array.from({ length: 9 }, (_, i) => ({
                        seriesLabel: `Trust ${i}`,
                        data: [{
                            xValue: 1,
                            yValue: i
                        }]
                    }))
                }
            },
            global: {
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })]
            }
        });
        await nextTick();
        await flushPromises();

        const opts = setOption.mock.calls[0][0];
        expect(opts.series[0].lineStyle.type).toBe("solid");
        expect(opts.series[8].itemStyle.color).toBe(CHART_SERIES_COLORS.light[0]);
        expect(opts.series[8].lineStyle.type).toBe("dashed");
    });

    it("guards the animation maths against an empty series", async () => {
        mount(AiModelMetricsChart, {
            props: {
                data: {
                    yLabel: "y",
                    xLabel: "x",
                    metrics: [{
                        seriesLabel: "empty",
                        data: []
                    }]
                }
            },
            global: {
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })]
            }
        });
        await nextTick();
        await flushPromises();

        // 3000 / 0 = Infinity would reach echarts as animationDuration.
        const opts = setOption.mock.calls[0][0];
        expect(Number.isFinite(opts.series[0].animationDuration)).toBe(true);
    });

    it("keeps the user's bar-plot toggle across data refreshes", async () => {
        const wrapper = mountChart();
        await nextTick();
        await flushPromises();

        // The user flips the toolbox magicType to "bar" — echarts announces it.
        const magicTypeHandler = on.mock.calls.find(([event]) => event === "magictypechanged")?.[1];
        expect(magicTypeHandler).toBeDefined();
        magicTypeHandler({ currentType: "bar" });

        // The 5s training poll delivers fresh data → the full options re-push
        // must respect the user's choice instead of stomping it back to line.
        await wrapper.setProps({
            data: {
                ...DATA,
                metrics: [...DATA.metrics]
            }
        });
        await flushPromises();

        const opts = setOption.mock.calls.at(-1)![0];
        expect(opts.series.every((s: { type: string }) => s.type === "bar")).toBe(true);
    });

    it("re-pushes the chart option on the 500ms post-mount tick", async () => {
        mountChart();
        await nextTick();
        await flushPromises();
        const beforeAdvance = setOption.mock.calls.length;

        vi.advanceTimersByTime(600);

        expect(setOption.mock.calls.length).toBeGreaterThan(beforeAdvance);
    });
});
