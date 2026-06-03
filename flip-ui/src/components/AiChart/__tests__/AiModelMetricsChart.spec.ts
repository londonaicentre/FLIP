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

const setOption = vi.fn();
const resize = vi.fn();

vi.mock("echarts/core", () => ({
    init: vi.fn(() => ({
        setOption,
        resize
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

    it("re-pushes the chart option on the 500ms post-mount tick", async () => {
        mountChart();
        await nextTick();
        await flushPromises();
        const beforeAdvance = setOption.mock.calls.length;

        vi.advanceTimersByTime(600);

        expect(setOption.mock.calls.length).toBeGreaterThan(beforeAdvance);
    });
});
