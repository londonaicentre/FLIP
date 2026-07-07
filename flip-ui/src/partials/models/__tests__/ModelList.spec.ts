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
vi.mock("swrv", () => ({
    default: () => ({
        data: mockData.ref,
        error: ref(null)
    })
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
                AiSearch: { template: "<input />" },
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
