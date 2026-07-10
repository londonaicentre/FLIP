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
import { ref } from "vue";

import ModelList from "../ModelList.vue";

const mockRoute = { params: { projectId: "project-1" } as Record<string, string> };
vi.mock("vue-router", async (importOriginal) => {
    const actual = await importOriginal<typeof import("vue-router")>();

    return {
        ...actual,
        useRoute: () => mockRoute
    };
});

const mockData = vi.hoisted(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const vue = require("vue") as typeof import("vue");

    return { ref: vue.ref<unknown>(undefined) };
});

// Captures the SWRV key function so tests can inspect the query the partial
// would fetch (page / search params).
const swrvKey: { fn?: () => string } = {};

vi.mock("swrv", () => ({
    default: (key: () => string) => {
        swrvKey.fn = key;

        return {
            data: mockData.ref,
            error: ref(null)
        };
    }
}));

const mockViewModel = vi.hoisted(() => vi.fn());
vi.mock("@/router", () => ({
    routeChange: { viewModel: mockViewModel },
    default: { push: vi.fn() }
}));

vi.mock("@/services/model-service", async (importOriginal) => {
    const actual = await importOriginal<typeof import("@/services/model-service")>();

    return {
        ...actual,
        getModels: vi.fn(async () => undefined)
    };
});

vi.mock("@/composables/useErrorHandler", () => ({ default: vi.fn() }));

function setData(v: unknown) {
    (mockData.ref as { value: unknown }).value = v;
}

function mountModelList() {
    return mount(ModelList, {
        global: {
            plugins: [
                createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false,
                    initialState: {
                        auth: {
                            user: {
                                username: "u",
                                userId: "id",
                                attributes: {
                                    sub: "s",
                                    email: "u@e.com"
                                },
                                permissions: ["CanCreateProjects"]
                            },
                            signInStep: "DONE",
                            mfaEnabled: true,
                            mfaRequired: true
                        }
                    }
                })
            ],
            stubs: {
                AiButton: { template: "<button><slot /></button>" },
                AiPagination: { template: "<div />" },
                AiSearch: {
                    props: ["modelValue"],
                    emits: ["update:modelValue"],
                    template: "<input data-test='model-search-input' :value='modelValue'"
                        + " @input='$emit(\"update:modelValue\", $event.target.value)' />"
                },
                AiSkeleton: { template: "<div />" },
                // VTable is a global component (registered via Vite) that the
                // unit-test runner doesn't auto-resolve. Stub it with a thin
                // table that forwards `data` into the body slot's `rows`.
                VTable: {
                    template: "<table><slot name=\"head\" /><tbody><slot name=\"body\" :rows=\"data ?? []\" /></tbody></table>",
                    props: ["data"]
                },
                "router-link": {
                    template: "<a><slot /></a>",
                    props: ["to"]
                }
            }
        }
    });
}

describe("ModelList — Status column", () => {
    beforeEach(() => {
        setData(undefined);
    });

    test("renders a Status column header after Description", async () => {
        setData({
            data: [{
                id: "m1",
                name: "A",
                description: "d",
                status: "PENDING"
            }]
        });
        const wrapper = mountModelList();
        await flushPromises();

        const headers = wrapper.findAll("th").map(th => th.text().trim());
        // Adjacent headers: Description, then Status, then the empty action column.
        const descIdx = headers.indexOf("Description");
        expect(descIdx).toBeGreaterThanOrEqual(0);
        expect(headers[descIdx + 1]).toBe("Status");
    });

    test("PENDING renders 'Model Created' with a tick icon", async () => {
        setData({
            data: [{
                id: "m1",
                name: "A",
                description: "",
                status: "PENDING"
            }]
        });
        const wrapper = mountModelList();
        await flushPromises();

        const cell = wrapper.find("[data-test='model-status-m1']");
        expect(cell.exists()).toBe(true);
        expect(cell.text()).toContain("Model Created");
        expect(cell.find("[data-test='model-status-icon-tick']").exists()).toBe(true);
        expect(cell.find("[data-test='model-status-icon-cross']").exists()).toBe(false);
    });

    test("INITIATED renders 'Model Queued' with a tick", async () => {
        setData({
            data: [{
                id: "m1",
                name: "A",
                description: "",
                status: "INITIATED"
            }]
        });
        const wrapper = mountModelList();
        await flushPromises();

        const cell = wrapper.find("[data-test='model-status-m1']");
        expect(cell.text()).toContain("Model Queued");
        expect(cell.find("[data-test='model-status-icon-tick']").exists()).toBe(true);
    });

    test("PREPARED renders 'Model Prepared' with a tick", async () => {
        setData({
            data: [{
                id: "m1",
                name: "A",
                description: "",
                status: "PREPARED"
            }]
        });
        const wrapper = mountModelList();
        await flushPromises();

        const cell = wrapper.find("[data-test='model-status-m1']");
        expect(cell.text()).toContain("Model Prepared");
        expect(cell.find("[data-test='model-status-icon-tick']").exists()).toBe(true);
    });

    test("TRAINING_STARTED renders 'Training Started' with a tick", async () => {
        setData({
            data: [{
                id: "m1",
                name: "A",
                description: "",
                status: "TRAINING_STARTED"
            }]
        });
        const wrapper = mountModelList();
        await flushPromises();

        const cell = wrapper.find("[data-test='model-status-m1']");
        expect(cell.text()).toContain("Training Started");
        expect(cell.find("[data-test='model-status-icon-tick']").exists()).toBe(true);
    });

    test("RESULTS_UPLOADED renders 'Results Uploaded' with a tick", async () => {
        setData({
            data: [{
                id: "m1",
                name: "A",
                description: "",
                status: "RESULTS_UPLOADED"
            }]
        });
        const wrapper = mountModelList();
        await flushPromises();

        const cell = wrapper.find("[data-test='model-status-m1']");
        expect(cell.text()).toContain("Results Uploaded");
        expect(cell.find("[data-test='model-status-icon-tick']").exists()).toBe(true);
    });

    test("ERROR renders 'Error' with a red cross", async () => {
        setData({
            data: [{
                id: "m1",
                name: "A",
                description: "",
                status: "ERROR"
            }]
        });
        const wrapper = mountModelList();
        await flushPromises();

        const cell = wrapper.find("[data-test='model-status-m1']");
        expect(cell.text()).toContain("Error");
        expect(cell.find("[data-test='model-status-icon-cross']").exists()).toBe(true);
        expect(cell.find("[data-test='model-status-icon-tick']").exists()).toBe(false);
    });

    test("STOPPED renders 'Stopped' with a red cross", async () => {
        setData({
            data: [{
                id: "m1",
                name: "A",
                description: "",
                status: "STOPPED"
            }]
        });
        const wrapper = mountModelList();
        await flushPromises();

        const cell = wrapper.find("[data-test='model-status-m1']");
        expect(cell.text()).toContain("Stopped");
        expect(cell.find("[data-test='model-status-icon-cross']").exists()).toBe(true);
    });

    test("missing status falls back to a placeholder rather than crashing", async () => {
        // Defensive: an older API response (or a stale cache from before the
        // backend started returning status) leaves the cell with neither a
        // tick nor a cross — and shouldn't blow up the row.
        setData({
            data: [{
                id: "m1",
                name: "A",
                description: ""
            }]
        });
        const wrapper = mountModelList();
        await flushPromises();

        expect(wrapper.exists()).toBe(true);
        const cell = wrapper.find("[data-test='model-status-m1']");
        expect(cell.exists()).toBe(true);
        expect(cell.find("[data-test='model-status-icon-tick']").exists()).toBe(false);
        expect(cell.find("[data-test='model-status-icon-cross']").exists()).toBe(false);
    });
});

describe("ModelList — mobile stacked rows (design 5a)", () => {
    beforeEach(() => {
        setData(undefined);
    });

    const MODELS = [
        {
            id: "m1",
            name: "Alpha",
            description: "First model",
            status: "TRAINING_STARTED",
            trusts: [
                {
                    id: "t1",
                    name: "King's College Hospital",
                    code: "KCH"
                },
                {
                    id: "t2",
                    name: "Guy's and St Thomas'",
                    code: "GSTT"
                }
            ]
        },
        {
            id: "m2",
            name: "Beta",
            description: "",
            status: "ERROR",
            trusts: []
        },
        {
            id: "m3",
            name: "Gamma",
            description: "Endpoint without trusts field",
            status: "PENDING"
        }
    ];

    test("stacks a mobile row per model and hides the table below sm", async () => {
        setData({
            data: MODELS,
            page: 1,
            totalPages: 1
        });
        const wrapper = mountModelList();
        await flushPromises();

        const list = wrapper.find("[data-test='model-stacked-list']");
        expect(list.classes()).toContain("sm:hidden");
        expect(wrapper.findAll("[data-test^='model-list-item-mobile-']")).toHaveLength(3);

        // Desktop table renders at sm+ only; its data-tests stay unique for Cypress.
        const tableWrap = wrapper.find("table").element.parentElement;
        expect(tableWrap?.className).toContain("hidden");
        expect(tableWrap?.className).toContain("sm:block");
    });

    test("pins the status chip with its dot on the first line", async () => {
        setData({ data: [MODELS[0]] });
        const wrapper = mountModelList();
        await flushPromises();

        const chip = wrapper.find("[data-test='model-status-chip-m1']");
        expect(chip.classes()).toContain("rounded-full");
        // The /models pill idiom: coloured chip + status dot inside it.
        expect(chip.classes().join(" ")).toContain("bg-fuchsia-100");
        expect(chip.find("span.rounded-full").classes().join(" ")).toContain("bg-fuchsia-500");
        expect(chip.text()).toContain("Training Started");
    });

    test("clamps the description to two lines and falls back to the placeholder", async () => {
        setData({ data: [MODELS[0], MODELS[1]] });
        const wrapper = mountModelList();
        await flushPromises();

        const rows = wrapper.findAll("[data-test^='model-list-item-mobile-']");
        expect(rows[0].find("p").classes()).toContain("line-clamp-2");
        expect(rows[0].text()).toContain("First model");
        expect(rows[1].text()).toContain("No description provided...");
    });

    test("renders trust chips per code, the empty copy for [], nothing when absent", async () => {
        setData({ data: MODELS });
        const wrapper = mountModelList();
        await flushPromises();

        const rows = wrapper.findAll("[data-test^='model-list-item-mobile-']");
        const chips = rows[0].findAll("[data-test='model-trust-chip']");
        expect(chips.map(c => c.text())).toEqual(["KCH", "GSTT"]);
        // Empty trusts array → the reserved empty-state line.
        expect(rows[1].text()).toContain("No Trusts assigned yet");
        // Endpoint not sending the field yet → no trusts line at all.
        expect(rows[2].findAll("[data-test='model-trust-chip']")).toHaveLength(0);
        expect(rows[2].text()).not.toContain("No Trusts assigned yet");
    });
});

describe("ModelList — interactions", () => {
    beforeEach(() => {
        setData(undefined);
        mockViewModel.mockClear();
    });

    test("row click navigates to the model in both presentations", async () => {
        setData({
            data: [{
                id: "m1",
                name: "Alpha",
                description: "First model",
                status: "PENDING"
            }]
        });
        const wrapper = mountModelList();
        await flushPromises();

        await wrapper.find("[data-test='model-list-item-0']").trigger("click");
        await wrapper.find("[data-test='model-list-item-mobile-0']").trigger("click");

        expect(mockViewModel).toHaveBeenCalledTimes(2);
        expect(mockViewModel).toHaveBeenCalledWith("project-1", "m1");
    });

    test("the Create Model button toggles the create-model modal store", async () => {
        setData({ data: [] });
        const wrapper = mountModelList();
        await flushPromises();

        await wrapper.find("[data-test='add-model-btn']").trigger("click");

        const modals = (await import("@/store/modals")).useModalsStore();
        expect(modals.toggleCreateModel).toHaveBeenCalled();
    });

    test("typing a search debounces into the fetch key and resets to page 1", async () => {
        vi.useFakeTimers();
        try {
            setData({ data: [] });
            const wrapper = mountModelList();

            await wrapper.find("[data-test='model-search']").setValue("alpha");
            expect(swrvKey.fn!()).not.toContain("&search=");

            vi.advanceTimersByTime(600);
            expect(swrvKey.fn!()).toContain("&search=alpha");
            expect(swrvKey.fn!()).toContain("pageNumber=1");

            // Clearing the box drops the search param again.
            await wrapper.find("[data-test='model-search']").setValue("");
            vi.advanceTimersByTime(600);
            expect(swrvKey.fn!()).not.toContain("&search=");
        }
        finally {
            vi.useRealTimers();
        }
    });
});

describe("ModelList — header Create-Model button", () => {
    beforeEach(() => {
        setData(undefined);
    });

    test("collapses its label below lg with an aria-label and an icon", async () => {
        setData({ data: [] });
        const wrapper = mountModelList();
        await flushPromises();

        const btn = wrapper.find("[data-test=add-model-btn]");
        expect(btn.exists()).toBe(true);
        expect(btn.attributes("aria-label")).toBe("Create Model");
        expect(btn.find("svg").exists()).toBe(true);
        const label = btn.find("span.hidden.lg\\:inline");
        expect(label.exists()).toBe(true);
        expect(label.text()).toBe("Create Model");
    });
});
