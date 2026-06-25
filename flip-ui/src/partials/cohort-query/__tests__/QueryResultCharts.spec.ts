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

const swrvMock = vi.hoisted(() => ({ value: null as unknown }));

vi.mock("swrv", async () => {
    const { ref } = await import("vue");

    return { default: () => ({ data: ref(swrvMock.value) }) };
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
});
