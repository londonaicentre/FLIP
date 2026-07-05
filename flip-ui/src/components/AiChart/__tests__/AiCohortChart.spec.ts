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

import AiCohortChart from "@/components/AiChart/AiCohortChart.vue";
import { CHART_SERIES_COLORS } from "@/components/AiChart/chartTheme";

// jsdom has no canvas — stub the echarts.init pipeline so the onMounted block
// can run without exercising a real renderer. We capture the last setOption
// payload to assert chart wiring (series names, legend, xAxis values).
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

// `useResizeObserver` would register a real ResizeObserver callback that we
// don't need; the debounced resize call is also irrelevant for coverage.
vi.mock("@vueuse/core", () => ({
    useResizeObserver: vi.fn(),
    useDebounceFn: (fn: unknown) => fn
}));

const DATA = {
    name: "age",
    results: [
        {
            trustId: "t1",
            trustName: "Trust One",
            data: [
                {
                    value: "<50",
                    count: 7
                },
                {
                    value: "50-70",
                    count: 12
                }
            ]
        },
        {
            trustId: "t2",
            trustName: "Trust Two",
            data: [{
                value: "<50",
                count: 3
            }]
        }
    ]
};

function mountChart(options: { withTrustStore?: boolean } = {}) {
    const { withTrustStore = true } = options;

    return mount(AiCohortChart, {
        props: { data: DATA },
        global: {
            plugins: [createTestingPinia({
                createSpy: vi.fn,
                stubActions: false,
                initialState: withTrustStore
                    ? {
                        trust: {
                            trusts: [{
                                id: "t1",
                                name: "Trust One",
                                code: "T1"
                            }]
                        }
                    }
                    : {}
            })]
        }
    });
}

describe("AiCohortChart", () => {
    beforeEach(() => {
        setOption.mockReset();
        resize.mockReset();
    });

    it("mounts and feeds option to echarts.init", async () => {
        const wrapper = mountChart();
        await nextTick();
        await flushPromises();

        expect(wrapper.exists()).toBe(true);
        expect(setOption).toHaveBeenCalled();
    });

    it("emits a series per trust and uses the trust code as the legend label when available", async () => {
        mountChart();
        await nextTick();
        await flushPromises();

        const opts = setOption.mock.calls[0][0];
        expect(opts.series).toHaveLength(2);
        // Trust One has a code in the trust store → label should be the code.
        expect(opts.series[0].name).toBe("T1");
        // Trust Two has no entry in the trust store → fall back to trustName.
        expect(opts.series[1].name).toBe("Trust Two");
        expect(opts.legend.data).toEqual(["T1", "Trust Two"]);
    });

    it("falls back to trustName when the trust store is empty", async () => {
        mountChart({ withTrustStore: false });
        await nextTick();
        await flushPromises();

        const opts = setOption.mock.calls[0][0];
        expect(opts.series[0].name).toBe("Trust One");
    });

    it("builds the xAxis from the unique sorted union of result values", async () => {
        mountChart();
        await nextTick();
        await flushPromises();

        const opts = setOption.mock.calls[0][0];
        expect(opts.xAxis.data).toEqual(["50-70", "<50"]);
    });

    it("themes series and chrome from the shared chart theme", async () => {
        mountChart();
        await nextTick();
        await flushPromises();

        const opts = setOption.mock.calls[0][0];
        expect(opts.color).toEqual([...CHART_SERIES_COLORS.light]);

        // The off-token fills/inks flagged in the dark-mode review must not resurface.
        const flattened = JSON.stringify(opts);
        for (const legacyHex of ["#111827", "#282A36", "#4A5462", "#61366e"]) {
            expect(flattened).not.toContain(legacyHex);
        }
    });
});
