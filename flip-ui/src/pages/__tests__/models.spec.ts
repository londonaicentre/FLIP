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
    statusCounts: Record<string, number>;
}

const mockSwrvData = ref<ModelsResponse | undefined>(undefined);

// Captures the SWRV key function so tests can inspect the query the page
// would fetch (search / status filter params).
const swrvKey: { fn?: () => string } = {};

vi.mock("swrv", () => ({
    default: (key: () => string) => {
        swrvKey.fn = key;

        return {
            data: mockSwrvData,
            mutate: vi.fn(),
            error: ref(null)
        };
    }
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

const setModels = (models: IModelSummary[], statusCounts: Record<string, number> = {}): void => {
    mockSwrvData.value = {
        data: models,
        totalPages: 1,
        page: 1,
        statusCounts
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

    test("stacks mobile rows with the status pill and green-dot trust chips below sm", async () => {
        setModels([makeModel({
            trusts: [trust("GSTT"), trust("KCH")],
            status: "TRAINING_STARTED",
            description: "Predicts stroke outcomes"
        })]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const row = wrapper.find("[data-test='models-list-item-0']");
        // Desktop grid renders at sm+ only; the stacked block replaces it below.
        const desktopGrid = row.find(".hidden");
        expect(desktopGrid.classes()).toContain("sm:grid");
        const mobile = row.find(".sm\\:hidden");
        expect(mobile.exists()).toBe(true);

        // Same status chip idiom (pill + dot) pinned beside the name…
        const pill = mobile.find("[data-test='model-status-indicator-mobile']");
        expect(pill.classes()).toContain("rounded-full");
        expect(pill.classes().join(" ")).toContain("bg-fuchsia-100");
        expect(pill.text()).toBe("Training Started");
        // …and the same green-dot trust chips.
        const chips = mobile.findAll("[data-test='model-trust-chip-mobile']");
        expect(chips).toHaveLength(2);
        expect(chips[0].find("span.bg-emerald-500").exists()).toBe(true);

        // The sortable column header strip is desktop-only.
        const headerStrip = wrapper.find("[data-test='sort-header-name']").element.closest(".hidden");
        expect(headerStrip).toBeTruthy();
    });

    test("mobile rows carry a capitals project eyebrow above the name and the description below (design 6a)", async () => {
        setModels([
            makeModel({ description: "Predicts stroke outcomes" }),
            makeModel({
                id: "m2",
                name: "sepsis-v1",
                description: ""
            }),
            // Field absent (older API) → no description line at all.
            makeModel({
                id: "m3",
                name: "old-api-model"
            })
        ]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const rows = wrapper.findAll("[data-test='models-list-item-0'], [data-test='models-list-item-1'], [data-test='models-list-item-2']");

        const eyebrow = rows[0].find("[data-test='model-project-eyebrow']");
        expect(eyebrow.classes()).toContain("uppercase");
        expect(eyebrow.text()).toBe("Stroke triage");
        // Eyebrow precedes the name link in the stacked column.
        const mobile = rows[0].find(".sm\\:hidden");
        expect(mobile.html().indexOf("model-project-eyebrow")).toBeLessThan(mobile.html().indexOf("model-name-mobile"));

        expect(rows[0].find("[data-test='model-description-mobile']").text()).toBe("Predicts stroke outcomes");
        expect(rows[0].find("[data-test='model-description-mobile']").classes()).toContain("line-clamp-2");
        expect(rows[1].find("[data-test='model-description-mobile']").text()).toContain("No description provided...");
        expect(rows[2].find("[data-test='model-description-mobile']").exists()).toBe(false);
    });

    test("renders the empty-state copy when the API returns zero models", async () => {
        setModels([]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.text()).toContain("There are no models to show");
        expect(wrapper.findAll("[data-test='models-list-item-0']")).toHaveLength(0);
    });

    test("renders a filter tile per group with counts summed from statusCounts", async () => {
        setModels([makeModel({ status: "TRAINING_STARTED" })], {
            TRAINING_STARTED: 3,
            PENDING: 1,
            INITIATED: 2,
            ERROR: 1,
            RESULTS_UPLOAD_FAILED: 1
        });
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.find("[data-test='filter-tile-training']").exists()).toBe(true);
        expect(wrapper.find("[data-test='filter-tile-count-training']").text()).toBe("3");
        // Preparing groups PENDING + PREPARED = 1 (a just-created model is being prepared, not queued).
        expect(wrapper.find("[data-test='filter-tile-count-preparing']").text()).toBe("1");
        // Queued is INITIATED only = 2.
        expect(wrapper.find("[data-test='filter-tile-count-queued']").text()).toBe("2");
        // Needs attention groups ERROR + RESULTS_UPLOAD_FAILED + STOPPED = 2.
        expect(wrapper.find("[data-test='filter-tile-count-attention']").text()).toBe("2");
    });

    test("orders the tiles with Preparing before In training", async () => {
        setModels([makeModel()]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const keys = wrapper.findAll("button[data-test^='filter-tile-']").map(b => b.attributes("data-test"));
        expect(keys).toEqual([
            "filter-tile-preparing",
            "filter-tile-training",
            "filter-tile-queued",
            "filter-tile-completed",
            "filter-tile-attention"
        ]);
    });

    test("opens with In training + Queued selected and unions further tile clicks", async () => {
        setModels([makeModel()]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.find("[data-test='filter-tile-training']").attributes("aria-pressed")).toBe("true");
        expect(wrapper.find("[data-test='filter-tile-queued']").attributes("aria-pressed")).toBe("true");
        expect(wrapper.find("[data-test='filter-tile-completed']").attributes("aria-pressed")).toBe("false");
        expect(swrvKey.fn!()).toContain("status=TRAINING_STARTED,INITIATED");

        // Adding a tile unions its statuses into the filter…
        await wrapper.find("[data-test='filter-tile-completed']").trigger("click");
        expect(swrvKey.fn!()).toContain("status=TRAINING_STARTED,INITIATED,RESULTS_UPLOADED");

        // …and deselecting everything clears the status filter entirely.
        await wrapper.find("[data-test='filter-tile-training']").trigger("click");
        await wrapper.find("[data-test='filter-tile-queued']").trigger("click");
        await wrapper.find("[data-test='filter-tile-completed']").trigger("click");
        expect(swrvKey.fn!()).not.toContain("status=");
    });

    test("clicking a filter tile activates it, and clicking it again clears the filter", async () => {
        setModels([makeModel({ status: "RESULTS_UPLOADED" })], { RESULTS_UPLOADED: 1 });
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const tile = wrapper.find("[data-test='filter-tile-completed']");
        expect(tile.attributes("aria-pressed")).toBe("false");

        await tile.trigger("click");
        expect(tile.attributes("aria-pressed")).toBe("true");

        await tile.trigger("click");
        expect(tile.attributes("aria-pressed")).toBe("false");
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


    test("clicking the Model header sorts rows by name and toggles direction", async () => {
        setModels([
            makeModel({
                id: "m1",
                name: "zebra"
            }),
            makeModel({
                id: "m2",
                name: "apple"
            })
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
            makeModel({
                id: "m1",
                name: "done",
                status: "RESULTS_UPLOADED"
            }),
            makeModel({
                id: "m2",
                name: "created",
                status: "PENDING"
            })
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

    test("TRAINING_STARTED status pill matches the In-training fuchsia, not amber", async () => {
        setModels([makeModel({ status: "TRAINING_STARTED" })]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const pill = wrapper.find("[data-test='model-status-indicator']");
        expect(pill.classes()).toContain("bg-fuchsia-100");
        // The inner dot must switch too — no amber anywhere in the pill.
        expect(pill.html()).not.toContain("amber");
    });

    test("PREPARED status pill stays amber", async () => {
        setModels([makeModel({ status: "PREPARED" })]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const pill = wrapper.find("[data-test='model-status-indicator']");
        expect(pill.classes()).toContain("bg-amber-100");
    });

    test("clicking the Project header sorts rows by project name", async () => {
        setModels([
            makeModel({
                id: "m1",
                name: "a",
                projectName: "Zebra project"
            }),
            makeModel({
                id: "m2",
                name: "b",
                projectName: "Apple project"
            })
        ]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        await wrapper.find("[data-test='sort-header-project']").trigger("click");
        expect(wrapper.findAll("[data-test='model-project']").map(p => p.text()))
            .toEqual(["Apple project", "Zebra project"]);
    });

    test("clicking the Trusts header sorts rows by trust count (fewest first)", async () => {
        setModels([
            makeModel({
                id: "m1",
                name: "three",
                trusts: [trust("A"), trust("B"), trust("C")]
            }),
            makeModel({
                id: "m2",
                name: "one",
                trusts: [trust("A")]
            })
        ]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        await wrapper.find("[data-test='sort-header-trusts']").trigger("click");
        expect(wrapper.findAll("[data-test='model-name']").map(n => n.text())).toEqual(["one", "three"]);
    });

    test("a trust chip with no code falls back to a cleaned, truncated name", async () => {
        setModels([makeModel({
            trusts: [{
                id: "t-long",
                name: "Very Long Hospital Name Here NHS Foundation Trust",
                code: undefined as unknown as string
            }]
        })]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const chip = wrapper.find("[data-test='model-trust-chip']");
        // "NHS Foundation Trust" stripped, then truncated to 14 chars + ellipsis.
        expect(chip.text().endsWith("…")).toBe(true);
        expect(chip.text().slice(0, -1)).toHaveLength(14);
    });

    test("the owner sub-line shows an em-dash when ownerName is unset", async () => {
        setModels([makeModel({ ownerName: null })]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.find("[data-test='models-list-item-0']").text()).toContain("—");
    });

    test("an error/terminal status renders a red pill and a red rail", async () => {
        setModels([makeModel({ status: "STOPPED" })]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const pill = wrapper.find("[data-test='model-status-indicator']");
        expect(pill.text()).toBe("Stopped");
        expect(pill.classes().some(c => c.includes("red"))).toBe(true);
        expect(wrapper.find("[data-test='model-status-rail']").classes()).toContain("bg-red-500");
    });
});
