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
import { beforeEach, describe, expect, test, vi } from "vitest";

import TrainingMetrics from "../TrainingMetrics.vue";

const mockRoute = { params: { modelId: "model-1" } as Record<string, string> };
vi.mock("vue-router", async (importOriginal) => {
    const actual = await importOriginal<typeof import("vue-router")>();

    return {
        ...actual,
        useRoute: () => mockRoute
    };
});

// Hoisted reactive ref drives the SWRV fetch — each test seeds it before mount.
const mockData = vi.hoisted(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const vue = require("vue") as typeof import("vue");

    return { ref: vue.ref<unknown>(undefined) };
});
vi.mock("swrv", () => ({
    default: () => ({
        data: mockData.ref,
        error: { value: null }
    })
}));

vi.mock("@/services/model-service", () => ({ getModelMetrics: vi.fn(async () => undefined) }));

// AiMetricsChart pulls chart.js + Vue-Chart-3 which are heavy and noisy in
// jsdom. Stub it down to a marker element whose attributes echo the chart
// payload, so tests can assert the active tab feeds it the right series.
vi.mock("@/components/AiChart/AiModelMetricsChart.vue", () => ({
    default: {
        name: "AiMetricsChart",
        props: ["data"],
        template: "<div data-test=\"chart-stub\" :data-y-label=\"data.yLabel\" :data-x-label=\"data.xLabel\" :data-series-labels=\"data.metrics.map(m => m.seriesLabel).join(',')\" />"
    }
}));

function setData(v: unknown) {
    (mockData.ref as { value: unknown }).value = v;
}

interface MountOptions {
    inProgress?: boolean;
    approvedTrusts?: Array<{ name: string; code?: string }>;
}

function mountTrainingMetrics({ inProgress = false, approvedTrusts = [] }: MountOptions = {}) {
    return mount(TrainingMetrics, {
        props: { inProgress },
        global: {
            plugins: [
                createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false,
                    initialState: {
                        project: {
                            project: {
                                id: "p-1",
                                approvedTrusts
                            }
                        }
                    }
                })
            ]
        }
    });
}

const TRAIN_LOSS = {
    yLabel: "TRAIN_LOSS",
    xLabel: "global_round",
    metrics: [
        {
            seriesLabel: "Kings College Hospital",
            data: [{
                xValue: 1,
                yValue: 0.5
            }]
        },
        {
            seriesLabel: "UCLH",
            data: [{
                xValue: 1,
                yValue: 0.6
            }]
        }
    ]
};
const VAL_F1 = {
    yLabel: "VAL-F1-SCORE",
    xLabel: "global_round",
    metrics: [{
        seriesLabel: "Kings College Hospital",
        data: [{
            xValue: 1,
            yValue: 0.8
        }]
    }]
};
// Same metric name (VAL_LOSS) under two different x-axis labels — must render as two separate plots
// (FLIP#148), with the tabs disambiguated by their x-label.
const VAL_LOSS_EPOCH = {
    yLabel: "VAL_LOSS",
    xLabel: "epoch",
    metrics: [{
        seriesLabel: "Kings College Hospital",
        data: [{
            xValue: 1,
            yValue: 0.4
        }]
    }]
};
const VAL_LOSS_ROUND = {
    yLabel: "VAL_LOSS",
    xLabel: "Global Round",
    metrics: [{
        seriesLabel: "Kings College Hospital",
        data: [{
            xValue: 1,
            yValue: 0.5
        }]
    }]
};

describe("TrainingMetrics", () => {
    beforeEach(() => {
        setData(undefined);
    });

    test("renders the empty-state copy when no metrics are available", async () => {
        setData([]);
        const wrapper = mountTrainingMetrics();
        await flushPromises();

        expect(wrapper.text()).toContain("Any metrics sent during the run will show here.");
        expect(wrapper.find("[role=tablist]").exists()).toBe(false);
    });

    test("renders the empty state as a tall centred placeholder with the chart icon above the copy", async () => {
        setData([]);
        const wrapper = mountTrainingMetrics();
        await flushPromises();

        const emptyState = wrapper.find("[data-test='metrics-empty-state']");
        for (const cls of ["min-h-[16rem]", "flex-col", "items-center", "justify-center", "rounded-lg"]) {
            expect(emptyState.classes()).toContain(cls);
        }

        const icon = emptyState.find("svg");
        for (const cls of ["w-12", "h-12", "mx-auto"]) {
            expect(icon.classes()).toContain(cls);
        }
    });

    test("renders one tab per yLabel and activates the first chart by default", async () => {
        setData([TRAIN_LOSS, VAL_F1]);
        const wrapper = mountTrainingMetrics();
        await flushPromises();

        const tabs = wrapper.findAll("[role=tab]");
        expect(tabs).toHaveLength(2);
        expect(tabs[0].text()).toBe("TRAIN_LOSS");
        expect(tabs[1].text()).toBe("VAL-F1-SCORE");

        expect(tabs[0].attributes("aria-selected")).toBe("true");
        expect(tabs[1].attributes("aria-selected")).toBe("false");

        expect(wrapper.find("[data-test=chart-stub]").attributes("data-y-label")).toBe("TRAIN_LOSS");
    });

    test("clicking a tab switches the active chart", async () => {
        setData([TRAIN_LOSS, VAL_F1]);
        const wrapper = mountTrainingMetrics();
        await flushPromises();

        await wrapper.get("[data-test=training-plot-tab-VAL-F1-SCORE-global_round]").trigger("click");
        await flushPromises();

        const tabs = wrapper.findAll("[role=tab]");
        expect(tabs[0].attributes("aria-selected")).toBe("false");
        expect(tabs[1].attributes("aria-selected")).toBe("true");
        expect(wrapper.find("[data-test=chart-stub]").attributes("data-y-label")).toBe("VAL-F1-SCORE");
    });

    test("translates trust display names to short codes in series labels", async () => {
        setData([TRAIN_LOSS]);
        const wrapper = mountTrainingMetrics({
            approvedTrusts: [
                {
                    name: "Kings College Hospital",
                    code: "KCH"
                },
                {
                    name: "UCLH",
                    code: "UCH"
                }
            ]
        });
        await flushPromises();

        // Both trust display names were rewritten to codes via projectStore.approvedTrusts.
        expect(wrapper.find("[data-test=chart-stub]").attributes("data-series-labels"))
            .toBe("KCH,UCH");
    });

    test("falls back to the raw seriesLabel when no code is mapped", async () => {
        setData([TRAIN_LOSS]);
        const wrapper = mountTrainingMetrics({
            approvedTrusts: [{
                name: "Kings College Hospital",
                code: "KCH"
            }]
            // UCLH intentionally omitted: no code mapping → renders the raw name.
        });
        await flushPromises();

        expect(wrapper.find("[data-test=chart-stub]").attributes("data-series-labels"))
            .toBe("KCH,UCLH");
    });

    test("falls back to the first chart when the active label disappears from the response", async () => {
        setData([TRAIN_LOSS, VAL_F1]);
        const wrapper = mountTrainingMetrics();
        await flushPromises();

        // Switch to the second tab so it has a non-default active label.
        await wrapper.get("[data-test=training-plot-tab-VAL-F1-SCORE-global_round]").trigger("click");
        await flushPromises();
        expect(wrapper.find("[data-test=chart-stub]").attributes("data-y-label")).toBe("VAL-F1-SCORE");

        // Backend drops VAL-F1-SCORE on the next refresh — the watcher should
        // re-pin the active label to the first chart still in the response.
        setData([TRAIN_LOSS]);
        await flushPromises();

        expect(wrapper.find("[data-test=chart-stub]").attributes("data-y-label")).toBe("TRAIN_LOSS");
    });

    test("clears the active chart when the response empties", async () => {
        setData([TRAIN_LOSS]);
        const wrapper = mountTrainingMetrics();
        await flushPromises();
        expect(wrapper.find("[data-test=chart-stub]").exists()).toBe(true);

        setData([]);
        await flushPromises();

        expect(wrapper.find("[data-test=chart-stub]").exists()).toBe(false);
        expect(wrapper.text()).toContain("Any metrics sent during the run will show here.");
    });
});

describe("TrainingMetrics plot layout", () => {
    beforeEach(() => {
        localStorage.clear();
        setData(undefined);
    });

    test("opens on the single-plot view, with the plot tabs to switch between them", async () => {
        setData([TRAIN_LOSS, VAL_F1]);
        const wrapper = mountTrainingMetrics();
        await flushPromises();

        expect(wrapper.find("[role=tablist]").exists()).toBe(true);
        expect(wrapper.findAll("[data-test=chart-stub]")).toHaveLength(1);
        expect(wrapper.find("[data-test=metrics-view-single]").attributes("aria-pressed")).toBe("true");
    });

    test("on a narrow window the plot is chosen from a dropdown, not from chips", async () => {
        setData([TRAIN_LOSS, VAL_F1]);
        const wrapper = mountTrainingMetrics();
        await flushPromises();

        // Dozens of chips scroll off a phone; a select holds them all.
        const select = wrapper.get("[data-test=metrics-plot-select]");
        expect(select.classes()).toContain("sm:hidden");
        expect(select.findAll("option").map(o => o.text())).toEqual(["TRAIN_LOSS", "VAL-F1-SCORE"]);

        const tabs = wrapper.get("[role=tablist]");
        expect(tabs.classes()).toContain("hidden");
        expect(tabs.classes()).toContain("sm:flex");
    });

    test("the dropdown runs up to the layout buttons, which stay pinned right", async () => {
        setData([TRAIN_LOSS, VAL_F1]);
        const wrapper = mountTrainingMetrics();
        await flushPromises();

        // A width cap on the select leaves the row's slack trailing behind the buttons,
        // stranding them mid-row instead of flush against the card's right edge.
        const select = wrapper.get("[data-test=metrics-plot-select]");
        expect(select.classes()).toContain("flex-1");
        expect(select.classes().some(c => c.startsWith("max-w-"))).toBe(false);

        expect(wrapper.get("[aria-label='Plot layout']").classes()).toContain("ml-auto");
    });

    test("choosing from the dropdown switches the plot", async () => {
        setData([TRAIN_LOSS, VAL_F1]);
        const wrapper = mountTrainingMetrics();
        await flushPromises();

        await wrapper.get("[data-test=metrics-plot-select]").setValue(JSON.stringify(["VAL-F1-SCORE", "global_round"]));

        expect(wrapper.get("[data-test=chart-stub]").attributes("data-y-label")).toBe("VAL-F1-SCORE");
    });

    test("the dropdown is gone in the grid, where every plot is already drawn", async () => {
        setData([TRAIN_LOSS, VAL_F1]);
        const wrapper = mountTrainingMetrics();
        await flushPromises();

        await wrapper.find("[data-test=metrics-view-grid]").trigger("click");

        expect(wrapper.find("[data-test=metrics-plot-select]").exists()).toBe(false);
    });

    test("the grid view draws every plot at once, each under its own label", async () => {
        setData([TRAIN_LOSS, VAL_F1]);
        const wrapper = mountTrainingMetrics();
        await flushPromises();

        await wrapper.find("[data-test=metrics-view-grid]").trigger("click");

        expect(wrapper.findAll("[data-test=chart-stub]")).toHaveLength(2);
        expect(wrapper.find("[data-test='metrics-grid-cell-TRAIN_LOSS-global_round']").text()).toContain("TRAIN_LOSS");
        expect(wrapper.find("[data-test='metrics-grid-cell-VAL-F1-SCORE-global_round']").text()).toContain("VAL-F1-SCORE");
        expect(wrapper.find("[data-test=metrics-view-grid]").attributes("aria-pressed")).toBe("true");
    });

    test("the grid cells hold their aspect ratio, so a lone wide column is not a flat sliver", async () => {
        setData([TRAIN_LOSS]);
        const wrapper = mountTrainingMetrics();
        await flushPromises();

        await wrapper.find("[data-test=metrics-view-grid]").trigger("click");

        const cell = wrapper.get("[data-test='metrics-grid-cell-TRAIN_LOSS-global_round']");
        expect(cell.classes()).not.toContain("h-56");
        // A stale canvas can never paint over the neighbouring cell.
        expect(cell.classes()).toContain("overflow-hidden");
        // The cells read as separate plots against the dark canvas.
        expect(cell.classes()).toContain("dark:ring-white/25");
        // The ratio is not on the cell: the caption and padding would eat into it,
        // leaving the plot itself flatter than 16:9.
        expect(cell.classes()).not.toContain("aspect-video");

        const plot = wrapper.get("[data-test='metrics-grid-plot-TRAIN_LOSS-global_round']");
        // One column below md, where a 16:9 plot spans the whole card and reads flat;
        // 16:9 returns once the columns do.
        expect(plot.classes()).toContain("aspect-[3/2]");
        expect(plot.classes()).toContain("md:aspect-video");
        expect(plot.classes()).toContain("min-h-[13rem]");
        expect(plot.classes()).toContain("max-h-[24rem]");

        // Load-bearing: without it the cells stretch to the grid's full height and the
        // plot is marooned in dead space.
        expect(wrapper.get("[data-test=metrics-grid]").classes()).toContain("items-start");
    });

    test("the grid scrolls rather than shrinking the plots, three across on a wide screen", async () => {
        setData([TRAIN_LOSS, VAL_F1]);
        const wrapper = mountTrainingMetrics();
        await flushPromises();

        await wrapper.find("[data-test=metrics-view-grid]").trigger("click");

        // Scrolling lives on the wrapper, never on the grid itself: a grid that is
        // its own fixed-height scroll container gets its auto rows sized by Chrome
        // to split the visible height, blind to the cells' aspect-ratio heights —
        // the cells then overflow their rows and paint over the cards below.
        const scroller = wrapper.get("[data-test=metrics-grid-scroller]");
        expect(scroller.classes()).toContain("overflow-y-auto");
        expect(scroller.classes()).toContain("min-h-0");

        const grid = wrapper.get("[data-test=metrics-grid]");
        for (const cls of ["overflow-y-auto", "flex-1", "min-h-0"]) {
            expect(grid.classes()).not.toContain(cls);
        }
        // The cells' rings sit outside their boxes; without side padding the scroll
        // container shears the leftmost column's ring off.
        expect(grid.classes()).toContain("px-1");
        expect(grid.classes()).toContain("grid-cols-1");
        expect(grid.classes()).toContain("2xl:grid-cols-3");
    });

    test("the plot tabs give way to the grid — with every plot drawn, picking one is meaningless", async () => {
        setData([TRAIN_LOSS, VAL_F1]);
        const wrapper = mountTrainingMetrics();
        await flushPromises();

        await wrapper.find("[data-test=metrics-view-grid]").trigger("click");

        expect(wrapper.find("[role=tablist]").exists()).toBe(false);
    });

    test("switching back to the single view returns you to the plot you had open", async () => {
        setData([TRAIN_LOSS, VAL_F1]);
        const wrapper = mountTrainingMetrics();
        await flushPromises();

        await wrapper.get("[data-test='training-plot-tab-VAL-F1-SCORE-global_round']").trigger("click");
        await wrapper.find("[data-test=metrics-view-grid]").trigger("click");
        await wrapper.find("[data-test=metrics-view-single]").trigger("click");

        expect(wrapper.get("[data-test=chart-stub]").attributes("data-y-label")).toBe("VAL-F1-SCORE");
    });

    test("the grid shortens trust names to their codes, exactly as the single view does", async () => {
        setData([TRAIN_LOSS]);
        const wrapper = mountTrainingMetrics({
            approvedTrusts: [{
                name: "Kings College Hospital",
                code: "KCH"
            }]
        });
        await flushPromises();

        await wrapper.find("[data-test=metrics-view-grid]").trigger("click");

        expect(wrapper.get("[data-test=chart-stub]").attributes("data-series-labels")).toBe("KCH,UCLH");
    });

    test("remembers the layout you chose, so leaving Run and coming back keeps it", async () => {
        setData([TRAIN_LOSS, VAL_F1]);
        const first = mountTrainingMetrics();
        await flushPromises();

        await first.find("[data-test=metrics-view-grid]").trigger("click");
        first.unmount();

        // A fresh mount — the component is torn down whenever you switch to Prepare.
        const second = mountTrainingMetrics();
        await flushPromises();

        expect(second.find("[data-test=metrics-grid]").exists()).toBe(true);
        expect(second.find("[data-test=metrics-view-grid]").attributes("aria-pressed")).toBe("true");
    });

    test("there is no layout switch when there is nothing to lay out", async () => {
        setData([]);
        const wrapper = mountTrainingMetrics();
        await flushPromises();

        expect(wrapper.find("[data-test=metrics-view-grid]").exists()).toBe(false);
    });
});

describe("TrainingMetrics chart sizing", () => {
    it("lets the chart grow into the card instead of locking it to a 16:9 box", () => {
        setData([TRAIN_LOSS]);
        const wrapper = mountTrainingMetrics();

        const panel = wrapper.get("[role=tabpanel]");
        expect(panel.classes()).toContain("flex-1");
        expect(panel.classes()).toContain("min-h-0");
        expect(panel.classes()).not.toContain("aspect-video");
    });

    test("renders a separate tab per (metric, x-label) and disambiguates same-metric tabs", async () => {
        setData([VAL_LOSS_EPOCH, VAL_LOSS_ROUND]);
        const wrapper = mountTrainingMetrics();
        await flushPromises();

        const tabs = wrapper.findAll("[role=tab]");
        // Two plots for the same metric name because their x-axis labels differ (FLIP#148).
        expect(tabs).toHaveLength(2);
        expect(tabs[0].text()).toBe("VAL_LOSS · epoch");
        expect(tabs[1].text()).toBe("VAL_LOSS · Global Round");

        // First plot active by default — identified by (yLabel, xLabel), not yLabel alone.
        const stub = wrapper.find("[data-test=chart-stub]");
        expect(stub.attributes("data-y-label")).toBe("VAL_LOSS");
        expect(stub.attributes("data-x-label")).toBe("epoch");
    });

    test("switches between same-metric plots that differ only by x-label", async () => {
        setData([VAL_LOSS_EPOCH, VAL_LOSS_ROUND]);
        const wrapper = mountTrainingMetrics();
        await flushPromises();

        await wrapper.findAll("[role=tab]")[1].trigger("click");
        await flushPromises();

        const stub = wrapper.find("[data-test=chart-stub]");
        expect(stub.attributes("data-y-label")).toBe("VAL_LOSS");
        expect(stub.attributes("data-x-label")).toBe("Global Round");
    });
});
