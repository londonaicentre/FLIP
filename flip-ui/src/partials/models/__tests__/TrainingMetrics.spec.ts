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
        template: "<div data-test=\"chart-stub\" :data-y-label=\"data.yLabel\" :data-series-labels=\"data.metrics.map(m => m.seriesLabel).join(',')\" />"
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
    xLabel: "globalRound",
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
    xLabel: "globalRound",
    metrics: [{
        seriesLabel: "Kings College Hospital",
        data: [{
            xValue: 1,
            yValue: 0.8
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

        await wrapper.get("[data-test=training-plot-tab-VAL-F1-SCORE]").trigger("click");
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
        await wrapper.get("[data-test=training-plot-tab-VAL-F1-SCORE]").trigger("click");
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

    test("the grid view draws every plot at once, each under its own label", async () => {
        setData([TRAIN_LOSS, VAL_F1]);
        const wrapper = mountTrainingMetrics();
        await flushPromises();

        await wrapper.find("[data-test=metrics-view-grid]").trigger("click");

        expect(wrapper.findAll("[data-test=chart-stub]")).toHaveLength(2);
        expect(wrapper.find("[data-test='metrics-grid-cell-TRAIN_LOSS']").text()).toContain("TRAIN_LOSS");
        expect(wrapper.find("[data-test='metrics-grid-cell-VAL-F1-SCORE']").text()).toContain("VAL-F1-SCORE");
        expect(wrapper.find("[data-test=metrics-view-grid]").attributes("aria-pressed")).toBe("true");
    });

    test("the grid cells hold their aspect ratio, so a lone wide column is not a flat sliver", async () => {
        setData([TRAIN_LOSS]);
        const wrapper = mountTrainingMetrics();
        await flushPromises();

        await wrapper.find("[data-test=metrics-view-grid]").trigger("click");

        const cell = wrapper.get("[data-test='metrics-grid-cell-TRAIN_LOSS']");
        expect(cell.classes()).not.toContain("h-56");
        // The ratio lives on the plot box, not the cell: a grid item is stretched to
        // its row, which overrides aspect-ratio outright.
        expect(cell.classes()).not.toContain("aspect-video");
        // A stale canvas can never paint over the neighbouring cell.
        expect(cell.classes()).toContain("overflow-hidden");
        // The cells read as separate plots against the dark canvas.
        expect(cell.classes()).toContain("dark:ring-white");

        const plot = wrapper.get("[data-test='metrics-grid-plot-TRAIN_LOSS']");
        // Height follows width, with a floor for a phone and a ceiling so one column
        // does not become a full-screen plot.
        expect(plot.classes()).toContain("aspect-video");
        expect(plot.classes()).toContain("min-h-[13rem]");
        expect(plot.classes()).toContain("max-h-[24rem]");
    });

    test("the grid scrolls rather than shrinking the plots, three across on a wide screen", async () => {
        setData([TRAIN_LOSS, VAL_F1]);
        const wrapper = mountTrainingMetrics();
        await flushPromises();

        await wrapper.find("[data-test=metrics-view-grid]").trigger("click");

        const grid = wrapper.get("[data-test=metrics-grid]");
        expect(grid.classes()).toContain("overflow-y-auto");
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

        await wrapper.get("[data-test='training-plot-tab-VAL-F1-SCORE']").trigger("click");
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
});
