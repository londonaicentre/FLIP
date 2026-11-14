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
import { vi } from "vitest";
import { nextTick } from "vue";

import QueryResultCharts from "@/partials/cohort-query/QueryResultCharts.vue";
import { useProjectStore } from "@/store/project";

const swrvMock = vi.hoisted(() => ({
    value: null as unknown,
    key: undefined as string | undefined
}));

vi.mock("swrv", async () => {
    const { ref } = await import("vue");

    return {
        default: (key: () => string) => {
            swrvMock.key = key();

            return { data: ref(swrvMock.value) };
        }
    };
});

const RESULTS_WITH_PLOTS = {
    recordCount: 42,
    trustsResults: [
        {
            name: "age",
            results: [{
                trustId: "t1",
                trustName: "T1",
                data: [{
                    value: "<50",
                    count: 7
                }]
            }]
        }
    ]
};

const stubs = {
    AiCard: { template: "<div data-test='ai-card'><slot /></div>" },
    AiChart: { template: "<div data-test='ai-chart' />" },
    AiLoader: { template: "<div data-test='ai-loader' />" },
    Transition: { template: "<div><slot /></div>" }
};

function mountCharts(options: {
    submitting?: boolean;
    data?: unknown;
    project?: object;
} = {}) {
    const {
        submitting = false,
        data = null,
        project = {
            id: "p-1",
            status: "UNSTAGED",
            query: { id: "q-1" }
        }
    } = options;
    swrvMock.value = data;

    return mount(QueryResultCharts, {
        props: { submitting },
        global: {
            plugins: [createTestingPinia({
                createSpy: vi.fn,
                stubActions: false,
                initialState: { project: { project } }
            })],
            stubs
        }
    });
}

describe("QueryResultCharts", () => {
    beforeEach(() => {
        swrvMock.value = null;
    });

    test("renders without errors", () => {
        const wrapper = mountCharts();
        expect(wrapper.exists()).toBe(true);
    });

    test("shows the loader while results are absent", () => {
        const wrapper = mountCharts();
        expect(wrapper.find("[data-test='ai-loader']").exists()).toBe(true);
    });

    test("shows the loader instead of stale plots while submitting", async () => {
        const wrapper = mountCharts({
            data: RESULTS_WITH_PLOTS,
            submitting: true
        });
        await nextTick();
        await flushPromises();

        expect(wrapper.find("[data-test='ai-loader']").exists()).toBe(true);
        expect(wrapper.find("[data-test='ai-chart']").exists()).toBe(false);
    });

    test("shows the plots once results land and submitting is false", async () => {
        const wrapper = mountCharts({ data: RESULTS_WITH_PLOTS });
        await nextTick();
        await flushPromises();

        expect(wrapper.find("[data-test='ai-loader']").exists()).toBe(false);
        expect(wrapper.find("[data-test='ai-chart']").exists()).toBe(true);
    });

    test("sizes plot cards by aspect ratio instead of a fixed height", async () => {
        const wrapper = mountCharts({ data: RESULTS_WITH_PLOTS });
        await nextTick();
        await flushPromises();

        // A fixed h-[500px] made narrow single-column plots absurdly tall; the
        // card follows a 4:3 ratio. No height cap: the auto-fill grid bounds the
        // card width, and a cap clipped wide cards out of their ratio.
        const card = wrapper.find("[data-test='chart-card']");
        expect(card.classes()).toContain("aspect-[4/3]");
        expect(card.classes()).not.toContain("max-h-[500px]");
        expect(card.classes()).not.toContain("h-[500px]");
    });

    test("lays plots out in an auto-fill grid rather than a fixed column count", async () => {
        const wrapper = mountCharts({ data: RESULTS_WITH_PLOTS });
        await nextTick();
        await flushPromises();

        // columns-1 lg:columns-2 capped wide screens at two plots and filled
        // column-first; the grid adds columns as width allows, filling row-first.
        const grid = wrapper.find("[data-test='chart-grid']");
        expect(grid.classes()).toContain("grid");
        expect(grid.classes()).toContain("grid-cols-[repeat(auto-fill,minmax(min(100%,420px),1fr))]");
        expect(grid.classes()).not.toContain("lg:columns-2");
    });

    test("polls the results of the project's current query, and nothing while it has none", () => {
        mountCharts();
        expect(swrvMock.key).toBe("/cohort/q-1");

        mountCharts({
            project: {
                id: "p-1",
                status: "UNSTAGED"
            }
        });
        expect(swrvMock.key).toBe("");
    });

    test("says so when the query returned too few records to plot", async () => {
        const wrapper = mountCharts({
            data: {
                recordCount: 0,
                trustsResults: []
            }
        });
        await flushPromises();

        expect(wrapper.find("[data-test='no-results-message']").text()).toContain("did not return enough records");
        expect(wrapper.find("[data-test='ai-chart']").exists()).toBe(false);
    });

    test("drops the plots for the loader when the query is re-run under a new id", async () => {
        const wrapper = mountCharts({ data: RESULTS_WITH_PLOTS });
        await flushPromises();
        expect(wrapper.find("[data-test='ai-chart']").exists()).toBe(true);

        useProjectStore().project!.query!.id = "q-2";
        await flushPromises();

        expect(wrapper.find("[data-test='ai-loader']").exists()).toBe(true);
        expect(wrapper.find("[data-test='ai-chart']").exists()).toBe(false);
    });

    test("frames each plot as a flat border box, not a shadowed card", async () => {
        const wrapper = mountCharts({ data: RESULTS_WITH_PLOTS });
        await nextTick();
        await flushPromises();

        const card = wrapper.find("[data-test='chart-card']");
        expect(card.classes()).toContain("border");
        expect(card.classes()).toContain("rounded-xl");
        expect(card.classes()).not.toContain("shadow");
    });
});
