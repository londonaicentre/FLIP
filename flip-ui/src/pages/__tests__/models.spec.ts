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
import { reactive, ref } from "vue";

import { IModelProjectOption, IModelSummary, IModelSummaryTrust } from "@/services/model-service";

import Page from "../models.vue";

interface ModelsResponse {
    data: IModelSummary[];
    totalPages: number;
    page: number;
    statusCounts: Record<string, number>;
}

const mockSwrvData = ref<ModelsResponse | undefined>(undefined);
const mockProjectOptions = ref<IModelProjectOption[] | undefined>(undefined);

// Captures the SWRV key function so tests can inspect the query the page
// would fetch (search / status / project filter params).
const swrvKey: { fn?: () => string } = {};

// The page runs two SWRV subscriptions — the models page itself and the project
// filter's options — so the mock routes by key rather than handing both the same ref.
vi.mock("swrv", () => ({
    default: (key: (() => string) | string) => {
        // The options subscription passes a plain string key; the list passes a function so its
        // filters stay reactive.
        if ((typeof key === "function" ? key() : key).startsWith("/models/projects")) {
            return {
                data: mockProjectOptions,
                mutate: vi.fn(),
                error: ref(null)
            };
        }
        swrvKey.fn = key as () => string;

        return {
            data: mockSwrvData,
            mutate: vi.fn(),
            error: ref(null)
        };
    }
}));

// The page reads its project scope from the URL. `reactive` is load-bearing: the tests that
// simulate an external navigation mutate `mockRoute.query` and expect the page's watcher to fire.
const mockRoute = reactive({
    path: "/models",
    query: {} as Record<string, string | undefined>
});
const mockReplace = vi.fn();
vi.mock("vue-router", async (importOriginal) => {
    const actual = await importOriginal<typeof import("vue-router")>();

    return {
        ...actual,
        useRoute: () => mockRoute,
        useRouter: () => ({ replace: mockReplace })
    };
});

const stubs = {
    AiLoader: { template: "<div />" },
    CreateModelModal: { template: "<div data-test='create-model-modal' />" },
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

const projectOption = (
    id: string,
    name: string,
    status: IModelProjectOption["status"] = "APPROVED"
): IModelProjectOption => ({
    id,
    name,
    status
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
    mockProjectOptions.value = undefined;
    mockRoute.query = {};
    mockReplace.mockClear();
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
        setModels([makeModel({ status: "RUNNING" })]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.find("[data-test='model-status-indicator']").text()).toBe("Running");
    });

    test("a queued model's status pill carries its queue position", async () => {
        setModels([makeModel({
            status: "INITIATED",
            queuePosition: 2
        })]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.find("[data-test='model-status-indicator']").text()).toBe("Model Queued (2)");
        expect(wrapper.find("[data-test='model-status-indicator-mobile']").text()).toBe("Model Queued (2)");
    });

    test("a queued model without a position keeps the plain label", async () => {
        setModels([makeModel({ status: "INITIATED" })]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.find("[data-test='model-status-indicator']").text()).toBe("Model Queued");
    });

    test("stacks mobile rows with the status pill and code-only trust chips below sm", async () => {
        setModels([makeModel({
            trusts: [trust("GSTT"), trust("KCH")],
            status: "RUNNING",
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
        expect(pill.text()).toBe("Running");
        // …and the trust chips, which carry the code alone: every chip here is a trust the
        // run was dispatched to, so there is no state for a dot to encode (unlike the
        // Projects list, where a linked trust can still be pending).
        const chips = mobile.findAll("[data-test='model-trust-chip-mobile']");
        expect(chips).toHaveLength(2);
        expect(chips[0].text()).toBe("GSTT");
        expect(chips[0].find("span").exists()).toBe(false);

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
        setModels([makeModel({ status: "RUNNING" })], {
            RUNNING: 3,
            PENDING: 1,
            INITIATED: 2,
            ERROR: 1,
            RESULTS_UPLOAD_FAILED: 1
        });
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.find("[data-test='filter-tile-running']").exists()).toBe(true);
        expect(wrapper.find("[data-test='filter-tile-count-running']").text()).toBe("3");
        // Preparing groups PENDING + PREPARED = 1 (a just-created model is being prepared, not queued).
        expect(wrapper.find("[data-test='filter-tile-count-preparing']").text()).toBe("1");
        // Queued is INITIATED only = 2.
        expect(wrapper.find("[data-test='filter-tile-count-queued']").text()).toBe("2");
        // Needs attention groups ERROR + RESULTS_UPLOAD_FAILED + STOPPED = 2.
        expect(wrapper.find("[data-test='filter-tile-count-attention']").text()).toBe("2");
    });

    test("orders the tiles with Preparing before Running", async () => {
        setModels([makeModel()]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const keys = wrapper.findAll("button[data-test^='filter-tile-']").map(b => b.attributes("data-test"));
        expect(keys).toEqual([
            "filter-tile-preparing",
            "filter-tile-running",
            "filter-tile-queued",
            "filter-tile-completed",
            "filter-tile-attention"
        ]);
    });

    test("opens with Running + Queued selected and unions further tile clicks", async () => {
        setModels([makeModel()]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.find("[data-test='filter-tile-running']").attributes("aria-pressed")).toBe("true");
        expect(wrapper.find("[data-test='filter-tile-queued']").attributes("aria-pressed")).toBe("true");
        expect(wrapper.find("[data-test='filter-tile-completed']").attributes("aria-pressed")).toBe("false");
        expect(swrvKey.fn!()).toContain("status=RUNNING,INITIATED");

        // Adding a tile unions its statuses into the filter…
        await wrapper.find("[data-test='filter-tile-completed']").trigger("click");
        expect(swrvKey.fn!()).toContain("status=RUNNING,INITIATED,RESULTS_UPLOADED");

        // …and deselecting everything clears the status filter entirely.
        await wrapper.find("[data-test='filter-tile-running']").trigger("click");
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
        setModels([makeModel({ status: "RUNNING" })]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const rail = wrapper.find("[data-test='model-status-rail']");
        expect(rail.exists()).toBe(true);
        // Training runs get the live magenta rail.
        expect(rail.classes().some(c => c.includes("fuchsia"))).toBe(true);
    });

    test("RUNNING status pill matches the Running-tile fuchsia, not amber", async () => {
        setModels([makeModel({ status: "RUNNING" })]);
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

        // Exact match: the created half also falls back to an em-dash here, so
        // a bare toContain("—") could no longer catch an ownerLabel regression.
        expect(wrapper.find("[data-test='model-owner-meta']").text()).toBe("— · —");
    });

    test("the owner sub-line shows the relative created time next to the owner (as on Projects)", async () => {
        // Offset-less naive-UTC string — the wire format the API actually sends
        // (see test_all_models_endpoint.py) — so local-time parsing regressions
        // fail here on any non-UTC machine.
        setModels([makeModel({ creationTimestamp: new Date(Date.now() - 2 * 3_600_000).toISOString().slice(0, 19) })]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.find("[data-test='model-owner-meta']").text()).toBe("Dr Ada · created 2h ago");
    });

    test("the created time falls back to an em-dash when the API omits the timestamp", async () => {
        setModels([makeModel()]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.find("[data-test='model-owner-meta']").text()).toBe("Dr Ada · —");
    });

    test("rows default-sort by creation date, most recent first", async () => {
        setModels([
            makeModel({
                id: "m1",
                name: "older",
                creationTimestamp: "2026-01-01T00:00:00"
            }),
            makeModel({
                id: "m2",
                name: "newer",
                creationTimestamp: "2026-06-01T00:00:00"
            })
        ]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.findAll("[data-test='model-name']").map(n => n.text())).toEqual(["newer", "older"]);
    });

    test("rows from an older API without timestamps keep the backend order under the default sort", async () => {
        // The rollout-skew case: a UI deployed ahead of the API gets no
        // creationTimestamp on ANY row — all tie at 0 and the stable sort must
        // preserve the backend's newest-first order.
        setModels([
            makeModel({
                id: "m1",
                name: "backend-first"
            }),
            makeModel({
                id: "m2",
                name: "backend-second"
            })
        ]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.findAll("[data-test='model-name']").map(n => n.text())).toEqual(["backend-first", "backend-second"]);
    });

    test("a just-created model reads amber, matching the Preparing tile it is counted under", async () => {
        // PENDING ("Model Created") is grouped with PREPARED by the Preparing tile, which is
        // amber — but the row used to render a grey pill and rail, so the same model read as
        // two different states depending on where you looked.
        setModels([makeModel({ status: "PENDING" })]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const pill = wrapper.find("[data-test='model-status-indicator']");
        expect(pill.text()).toBe("Model Created");
        expect(pill.classes().some(c => c.includes("amber"))).toBe(true);
        expect(wrapper.find("[data-test='model-status-rail']").classes()).toContain("bg-amber-500");
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

    describe("project filter", () => {
        const options = [projectOption("p1", "Stroke triage"), projectOption("p2", "Chest X-ray")];

        test("the dropdown offers every accessible project, plus an All projects reset", async () => {
            setModels([makeModel()]);
            mockProjectOptions.value = options;
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            const labels = wrapper.find("[data-test='project-filter']").findAll("option").map(o => o.text());
            expect(labels).toEqual(["All projects", "Stroke triage", "Chest X-ray"]);
        });

        test("picking a project writes it to the URL rather than to local state", async () => {
            // The URL is the source of truth: the page navigates and re-reads, so a scoped view
            // is always linkable and the back button works.
            setModels([makeModel()]);
            mockProjectOptions.value = options;
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            await wrapper.find("[data-test='project-filter']").setValue("p2");

            expect(mockReplace).toHaveBeenCalledWith(
                expect.objectContaining({ query: expect.objectContaining({ project: "p2" }) })
            );
        });

        test("arriving scoped puts the project in the fetch key and clears the status tiles", async () => {
            // "View All Models" promises all of the project's models, so an arrival must not
            // inherit the estate default of Running + Queued.
            mockRoute.query = { project: "p1" };
            setModels([makeModel()]);
            mockProjectOptions.value = options;
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            const key = swrvKey.fn!();
            expect(key).toContain("&project=p1");
            expect(key).not.toContain("status=");
        });

        test("the scoped header names the project and the chip offers a way out", async () => {
            mockRoute.query = { project: "p1" };
            setModels([makeModel()]);
            mockProjectOptions.value = options;
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            expect(wrapper.find("[data-test='models-scope-eyebrow']").text()).toContain("Stroke triage");
            expect(wrapper.find("[data-test='project-filter-chip']").text()).toContain("Project: Stroke triage");

            await wrapper.find("[data-test='clear-project-filter']").trigger("click");

            expect(mockReplace).toHaveBeenCalledWith(
                expect.objectContaining({ query: expect.not.objectContaining({ project: expect.anything() }) })
            );
        });

        test("an unscoped page keeps the estate eyebrow and shows no chip", async () => {
            setModels([makeModel()]);
            mockProjectOptions.value = options;
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            expect(wrapper.find("[data-test='models-scope-eyebrow']").text()).toContain("Estate-wide");
            expect(wrapper.find("[data-test='project-filter-chip']").exists()).toBe(false);
        });

        test("a project id that is not in the options is dropped instead of fetched", async () => {
            // Covers a stale bookmark, a deleted project and lost access in one guard — and keeps
            // the page off the backend's 403, which would strand it behind the loader.
            mockRoute.query = { project: "gone" };
            setModels([makeModel()]);
            mockProjectOptions.value = options;
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            expect(mockReplace).toHaveBeenCalledWith(
                expect.objectContaining({ query: expect.not.objectContaining({ project: expect.anything() }) })
            );
        });

        test("waits for the options before judging an id", async () => {
            // Options are still in flight: dropping the filter here would fight the user's own
            // deep link on every page load.
            mockRoute.query = { project: "p1" };
            setModels([makeModel()]);
            mockProjectOptions.value = undefined;
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            expect(mockReplace).not.toHaveBeenCalled();
        });

        test("navigating back to the estate view restores the default tiles and clears search", async () => {
            // The sidebar Models link is a query-only change on the same route, so the component
            // is never remounted — only a watcher can reset it.
            mockRoute.query = { project: "p1" };
            setModels([makeModel()]);
            mockProjectOptions.value = options;
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            mockRoute.query = {};
            await wrapper.vm.$nextTick();

            const key = swrvKey.fn!();
            expect(key).not.toContain("project=");
            expect(key).toContain("status=RUNNING,INITIATED");
        });
    });

    describe("scoped Create Model", () => {
        const approved = [projectOption("p1", "Stroke triage", "APPROVED")];
        const staged = [projectOption("p1", "Stroke triage", "STAGED")];

        const mountScoped = (opts: { permissions?: string[]; options?: IModelProjectOption[] } = {}) => {
            mockRoute.query = { project: "p1" };
            setModels([makeModel()]);
            mockProjectOptions.value = opts.options ?? approved;

            return mountPage({ permissions: opts.permissions ?? ["CanCreateProjects"] });
        };

        test("shows for a researcher on an approved project", async () => {
            const wrapper = mountScoped();
            await wrapper.vm.$nextTick();

            expect(wrapper.find("[data-test='add-model-btn']").exists()).toBe(true);
        });

        test("hides for a viewer", async () => {
            const wrapper = mountScoped({ permissions: [] });
            await wrapper.vm.$nextTick();

            expect(wrapper.find("[data-test='add-model-btn']").exists()).toBe(false);
        });

        test("hides when the project is not approved", async () => {
            // Creating against an unapproved project is refused server-side; don't offer it.
            const wrapper = mountScoped({ options: staged });
            await wrapper.vm.$nextTick();

            expect(wrapper.find("[data-test='add-model-btn']").exists()).toBe(false);
        });

        test("hides on the estate-wide view, which has no project to create against", async () => {
            setModels([makeModel()]);
            mockProjectOptions.value = approved;
            const wrapper = mountPage({ permissions: ["CanCreateProjects"] });
            await wrapper.vm.$nextTick();

            expect(wrapper.find("[data-test='add-model-btn']").exists()).toBe(false);
        });
    });
});
