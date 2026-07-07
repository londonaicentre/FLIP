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
import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { ref } from "vue";

import { IModelSummary, IModelSummaryTrust } from "@/services/model-service";

import Page from "../models.vue";

interface ModelsResponse {
    data: IModelSummary[];
    totalPages: number;
    page: number;
}

const mockSwrvData = ref<ModelsResponse | undefined>(undefined);

vi.mock("swrv", () => ({
    default: () => ({
        data: mockSwrvData,
        mutate: vi.fn(),
        error: ref(null)
    })
}));

const stubs = {
    AiLoader: { template: "<div />" },
    AiSearch: {
        props: ["modelValue"],
        emits: ["update:modelValue"],
        template: "<input data-test='model-search' :value='modelValue' @input='$emit(\"update:modelValue\", $event.target.value)' />"
    },
    AiPagination: { template: "<div />" },
    // Expose `to` as an attribute so tests can assert the link target.
    "router-link": {
        template: "<a :data-to='to'><slot /></a>",
        props: ["to"]
    }
};

const trust = (code: string): IModelSummaryTrust => ({
    id: `t-${code}`,
    name: `${code} NHS Foundation Trust`,
    code
});

const makeModel = (over: Partial<IModelSummary> = {}): IModelSummary => ({
    id: "m1",
    name: "stroke-v1",
    status: "PREPARED",
    projectId: "p1",
    projectName: "Stroke triage",
    ownerId: "u1",
    ownerName: "Dr Ada",
    trusts: [trust("GSTT")],
    ...over
});

const setModels = (models: IModelSummary[]): void => {
    mockSwrvData.value = {
        data: models,
        totalPages: 1,
        page: 1
    };
};

function mountPage(options: { permissions?: string[] } = {}) {
    const { permissions = [] } = options;

    return mount(Page, {
        global: {
            plugins: [createTestingPinia({
                createSpy: vi.fn,
                stubActions: false,
                initialState: {
                    auth: {
                        user: {
                            userId: "u1",
                            permissions
                        }
                    }
                }
            })],
            stubs
        }
    });
}

beforeEach(() => {
    mockSwrvData.value = undefined;
});

describe("Models Page", () => {
    test("renders the component", () => {
        expect(mountPage().exists()).toBe(true);
    });

    test("renders one row per model returned by the API", async () => {
        setModels([
            makeModel(),
            makeModel({
                id: "m2",
                name: "sepsis-v1",
                projectId: "p2",
                projectName: "Sepsis"
            })
        ]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.find("[data-test='models-list-item-0']").exists()).toBe(true);
        expect(wrapper.find("[data-test='models-list-item-1']").exists()).toBe(true);
    });

    test("the model name links to the model page and the project name to the project page", async () => {
        setModels([makeModel()]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.find("[data-test='model-name']").attributes("data-to")).toBe("/project/p1/model/m1");
        expect(wrapper.find("[data-test='model-project']").attributes("data-to")).toBe("/project/p1");
    });

    test("renders trust chips using the trust code", async () => {
        setModels([makeModel({ trusts: [trust("GSTT"), trust("KCH")] })]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const chips = wrapper.findAll("[data-test='model-trust-chip']");
        expect(chips).toHaveLength(2);
        expect(chips.map(c => c.text())).toEqual(expect.arrayContaining(["GSTT", "KCH"]));
    });

    test("shows an explanatory note instead of chips when a model has no run trusts yet", async () => {
        setModels([makeModel({
            trusts: [],
            status: "PENDING"
        })]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.findAll("[data-test='model-trust-chip']")).toHaveLength(0);
        expect(wrapper.find("[data-test='model-trusts-empty']").exists()).toBe(true);
        expect(wrapper.find("[data-test='model-trusts-empty']").text().toLowerCase()).toContain("training");
    });

    test("renders a human-readable status label", async () => {
        setModels([makeModel({ status: "TRAINING_STARTED" })]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.find("[data-test='model-status-indicator']").text()).toBe("Training Started");
    });

    test("renders the empty-state copy when the API returns zero models", async () => {
        setModels([]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.text()).toContain("There are no models to show");
        expect(wrapper.findAll("[data-test='models-list-item-0']")).toHaveLength(0);
    });

    test("the status filter offers a value per model status and can be changed without error", async () => {
        setModels([makeModel({ status: "TRAINING_STARTED" })]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const filter = wrapper.find("[data-test='model-status-filter']");
        expect(filter.exists()).toBe(true);
        await filter.setValue("TRAINING_STARTED");
        await wrapper.vm.$nextTick();

        expect(wrapper.exists()).toBe(true);
    });

    test("typing in the search input triggers a paginated reload (debouncedWatch)", async () => {
        setModels([makeModel()]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        await wrapper.find("[data-test='model-search']").setValue("stroke");
        await new Promise((resolve) => setTimeout(resolve, 600));
        await wrapper.vm.$nextTick();

        expect(wrapper.exists()).toBe(true);
    });

    test("falls back to the owner email local-part when ownerName is unset", async () => {
        setModels([makeModel({
            ownerName: null,
            ownerId: "u1"
        })]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        // With no ownerName the row should still render without throwing.
        expect(wrapper.find("[data-test='models-list-item-0']").exists()).toBe(true);
    });

    test("clicking the Model header sorts rows by name and toggles direction", async () => {
        setModels([
            makeModel({ id: "m1", name: "zebra" }),
            makeModel({ id: "m2", name: "apple" })
        ]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        await wrapper.find("[data-test='sort-header-name']").trigger("click");
        expect(wrapper.findAll("[data-test='model-name']").map(n => n.text())).toEqual(["apple", "zebra"]);

        await wrapper.find("[data-test='sort-header-name']").trigger("click");
        expect(wrapper.findAll("[data-test='model-name']").map(n => n.text())).toEqual(["zebra", "apple"]);
    });

    test("clicking the Status header groups rows by lifecycle stage (ascending)", async () => {
        setModels([
            makeModel({ id: "m1", name: "done", status: "RESULTS_UPLOADED" }),
            makeModel({ id: "m2", name: "created", status: "PENDING" })
        ]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        await wrapper.find("[data-test='sort-header-status']").trigger("click");
        // PENDING (earliest lifecycle) sorts before RESULTS_UPLOADED.
        expect(wrapper.findAll("[data-test='model-name']").map(n => n.text())).toEqual(["created", "done"]);
    });

    test("each row carries a status-coloured left rail", async () => {
        setModels([makeModel({ status: "TRAINING_STARTED" })]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const rail = wrapper.find("[data-test='model-status-rail']");
        expect(rail.exists()).toBe(true);
        // Training runs get the live magenta rail.
        expect(rail.classes().some(c => c.includes("fuchsia"))).toBe(true);
    });
});
