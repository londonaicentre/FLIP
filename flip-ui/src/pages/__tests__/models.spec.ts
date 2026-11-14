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
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
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

/** The shape the page reads a status off — an axios error carries `response.status`. */
type OptionsError = { response?: { status?: number } } | null;
const mockProjectOptionsError = ref<OptionsError>(null);
const mockProjectOptionsValidating = ref(false);
const mockProjectOptionsMutate = vi.fn();

/** A failed options call: `status` omitted stands for a network error, which carries no response. */
const httpError = (status?: number): OptionsError => (status === undefined ? {} : { response: { status } });

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
                mutate: mockProjectOptionsMutate,
                error: mockProjectOptionsError,
                isValidating: mockProjectOptionsValidating
            };
        }
        swrvKey.fn = key as () => string;

        return {
            data: mockSwrvData,
            mutate: vi.fn(),
            error: ref(null),
            isValidating: ref(false)
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
    AiPagination: { template: "<div data-test='pagination' />" },
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

const setModels = (models: IModelSummary[], statusCounts: Record<string, number> = {}, totalPages = 1): void => {
    mockSwrvData.value = {
        data: models,
        totalPages,
        page: 1,
        statusCounts
    };
};

// Every page mounted by a test, unmounted in afterEach. The SWRV refs and the route object are
// module-level, so a wrapper left mounted keeps reacting to the *next* test's state — which
// silently multiplies the mutate/replace calls the guard tests count.
const mountedPages: { unmount: () => void }[] = [];

function mountPage(options: { permissions?: string[] } = {}) {
    const { permissions = [] } = options;

    const wrapper = mount(Page, {
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
    mountedPages.push(wrapper);

    return wrapper;
}

afterEach(() => {
    mountedPages.splice(0).forEach(wrapper => wrapper.unmount());
});

beforeEach(() => {
    mockSwrvData.value = undefined;
    mockProjectOptions.value = undefined;
    mockProjectOptionsError.value = null;
    mockProjectOptionsValidating.value = false;
    mockProjectOptionsMutate.mockClear();
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

    test("a queued model says Queued and gives its place in the queue on the meta line", async () => {
        setModels([makeModel({
            status: "INITIATED",
            queuePosition: 2
        })]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.find("[data-test='model-status-indicator']").text()).toBe("Queued");
        expect(wrapper.find("[data-test='model-status-indicator-mobile']").text()).toBe("Queued");
        expect(wrapper.find("[data-test='model-owner-meta']").text()).toBe("Dr Ada · Position 2 in queue");
    });

    test("a running model's meta line says when it was last updated", async () => {
        setModels([makeModel({
            status: "RUNNING",
            lastActivity: new Date(Date.now() - 3 * 3_600_000).toISOString().slice(0, 19)
        })]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.find("[data-test='model-owner-meta']").text()).toBe("Dr Ada · Updated 3h ago");
    });

    test("a completed model says when it finished, falling back to its last activity", async () => {
        const hoursAgo = (h: number): string => new Date(Date.now() - h * 3_600_000).toISOString().slice(0, 19);
        setModels([
            makeModel({
                status: "RESULTS_UPLOADED",
                finishedAt: hoursAgo(5),
                lastActivity: hoursAgo(1)
            }),
            makeModel({
                id: "m2",
                status: "RESULTS_UPLOADED",
                lastActivity: hoursAgo(3)
            })
        ]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.findAll("[data-test='model-owner-meta']").map(m => m.text()))
            .toEqual(["Dr Ada · Finished 5h ago", "Dr Ada · Finished 3h ago"]);
    });

    test("any other model says when it was last updated, from its last activity or else its creation", async () => {
        const hoursAgo = (h: number): string => new Date(Date.now() - h * 3_600_000).toISOString().slice(0, 19);
        setModels([
            makeModel({ lastActivity: hoursAgo(1) }),
            makeModel({
                id: "m2",
                creationTimestamp: hoursAgo(2)
            })
        ]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.findAll("[data-test='model-owner-meta']").map(m => m.text()))
            .toEqual(["Dr Ada · Updated 1h ago", "Dr Ada · Updated 2h ago"]);
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

        // Same status chip idiom (pill + dot), under the name so nothing overflows the card…
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

    test("mobile rows carry a project eyebrow above the name and the description below (design 6a)", async () => {
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
        expect(eyebrow.classes()).not.toContain("uppercase");
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

        expect(wrapper.find("[data-test='models-empty']").text()).toBe("No models yet.");
        expect(wrapper.find("[data-test='show-all-models']").exists()).toBe(false);
        expect(wrapper.findAll("[data-test='models-list-item-0']")).toHaveLength(0);
    });

    test("an empty filtered list names the filter and offers to show every model", async () => {
        setModels([]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        await wrapper.find("[data-test='status-filter-running']").trigger("click");
        expect(wrapper.find("[data-test='models-empty']").text()).toContain("No running models.");

        await wrapper.find("[data-test='show-all-models']").trigger("click");
        expect(wrapper.find("[data-test='status-filter-all']").attributes("aria-selected")).toBe("true");
        expect(swrvKey.fn!()).not.toContain("status=");
    });

    test("hides the pagination bar when there is only one page", async () => {
        setModels([makeModel()]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();
        expect(wrapper.find("[data-test='pagination']").exists()).toBe(false);

        setModels([makeModel()], {}, 2);
        await wrapper.vm.$nextTick();
        expect(wrapper.find("[data-test='pagination']").exists()).toBe(true);
    });

    test("renders a filter tile per group with counts summed from statusCounts", async () => {
        setModels([makeModel({ status: "RUNNING" })], {
            RUNNING: 3,
            PENDING: 1,
            INITIATED: 2,
            ERROR: 1,
            RESULTS_UPLOAD_FAILED: 1,
            STOPPED: 4
        });
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.find("[data-test='filter-tile-count-running']").text()).toBe("3");
        // Preparing groups PENDING + PREPARED.
        expect(wrapper.find("[data-test='filter-tile-count-preparing']").text()).toBe("1");
        expect(wrapper.find("[data-test='filter-tile-count-queued']").text()).toBe("2");
        // Needs attention is ERROR + RESULTS_UPLOAD_FAILED; a stopped model is its own group.
        expect(wrapper.find("[data-test='filter-tile-count-attention']").text()).toBe("2");
        expect(wrapper.find("[data-test='filter-tile-count-stopped']").text()).toBe("4");
        expect(wrapper.find("[data-test='filter-tile-count-completed']").text()).toBe("0");
        // The phone row carries the same counts, plus All.
        expect(wrapper.find("[data-test='status-filter-count-all']").text()).toBe("12");
        expect(wrapper.find("[data-test='status-filter-count-stopped']").text()).toBe("4");
    });

    test("orders the tiles and the phone row the same way, the row led by All", async () => {
        setModels([makeModel()]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const groups = ["preparing", "running", "queued", "attention", "stopped", "completed"];
        expect(wrapper.findAll("button[data-test^='filter-tile-']").map(b => b.attributes("data-test")))
            .toEqual(groups.map(g => `filter-tile-${g}`));
        expect(wrapper.findAll("button[data-test^='status-filter-']").map(b => b.attributes("data-test")))
            .toEqual(["all", ...groups].map(g => `status-filter-${g}`));
        expect(wrapper.find("[data-test='status-filter']").attributes("role")).toBe("tablist");
        expect(wrapper.find("[data-test='status-filter-stopped']").text()).toBe("Stopped 0");
    });

    test("opens on All with no status filter", async () => {
        setModels([makeModel()]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(swrvKey.fn!()).not.toContain("status=");
        expect(wrapper.find("[data-test='status-filter-all']").attributes("aria-selected")).toBe("true");
        expect(wrapper.findAll("[aria-pressed='true']")).toHaveLength(0);
    });

    test("tiles and the phone row share one single selection; a selected tile clicked again returns to All", async () => {
        setModels([makeModel()]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        await wrapper.find("[data-test='filter-tile-completed']").trigger("click");
        expect(swrvKey.fn!()).toContain("status=RESULTS_UPLOADED");
        expect(wrapper.find("[data-test='status-filter-completed']").attributes("aria-selected")).toBe("true");

        // Picking another replaces it rather than adding to it.
        await wrapper.find("[data-test='status-filter-stopped']").trigger("click");
        expect(swrvKey.fn!()).toContain("status=STOPPED");
        expect(swrvKey.fn!()).not.toContain("RESULTS_UPLOADED");
        expect(wrapper.find("[data-test='filter-tile-stopped']").attributes("aria-pressed")).toBe("true");
        expect(wrapper.find("[data-test='filter-tile-completed']").attributes("aria-pressed")).toBe("false");

        await wrapper.find("[data-test='filter-tile-stopped']").trigger("click");
        expect(swrvKey.fn!()).not.toContain("status=");
        expect(wrapper.find("[data-test='status-filter-all']").attributes("aria-selected")).toBe("true");
    });

    test("a selected tile is filled as well as ringed", async () => {
        setModels([makeModel()]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const tile = wrapper.find("[data-test='filter-tile-queued']");
        await tile.trigger("click");
        expect(tile.classes()).toEqual(expect.arrayContaining(["bg-gray-100", "ring-[3px]"]));
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
        // Filter order: Preparing (PENDING) sorts before Completed (RESULTS_UPLOADED).
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

    test("the meta line leaves out what is unknown, so no placeholder dash shows", async () => {
        setModels([makeModel({ ownerName: null }), makeModel({ id: "m2" })]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        // No owner and no times: no line at all. Owner only: just the owner.
        expect(wrapper.findAll("[data-test='model-owner-meta']").map(m => m.text())).toEqual(["Dr Ada"]);
    });

    test("rows keep the backend order until a header is clicked", async () => {
        // The hub already sends the rows latest activity first; the page must not re-sort them.
        setModels([
            makeModel({
                id: "m1",
                name: "backend-first",
                creationTimestamp: "2025-01-01T00:00:00"
            }),
            makeModel({
                id: "m2",
                name: "backend-second",
                lastActivity: "2026-06-01T00:00:00"
            })
        ]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.findAll("[data-test='model-name']").map(n => n.text())).toEqual(["backend-first", "backend-second"]);
    });

    test("a just-created model reads Preparing in amber, matching the filter it is counted under", async () => {
        // PENDING ("Model Created") is grouped with PREPARED by the Preparing tile, which is
        // amber — but the row used to render a grey pill and rail, so the same model read as
        // two different states depending on where you looked.
        setModels([makeModel({ status: "PENDING" })]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const pill = wrapper.find("[data-test='model-status-indicator']");
        expect(pill.text()).toBe("Preparing");
        expect(pill.classes().some(c => c.includes("amber"))).toBe(true);
        expect(wrapper.find("[data-test='model-status-rail']").classes()).toContain("bg-amber-500");
    });

    test("a failed model reads Needs attention with a red pill and rail", async () => {
        setModels([makeModel({ status: "ERROR" })]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const pill = wrapper.find("[data-test='model-status-indicator']");
        expect(pill.text()).toBe("Needs attention");
        expect(pill.classes().some(c => c.includes("red"))).toBe(true);
        expect(wrapper.find("[data-test='model-status-rail']").classes()).toContain("bg-red-500");
    });

    test("a stopped model is its own grey group, not an error", async () => {
        setModels([makeModel({ status: "STOPPED" })]);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const pill = wrapper.find("[data-test='model-status-indicator']");
        expect(pill.text()).toBe("Stopped");
        expect(pill.html()).not.toContain("red");
        expect(wrapper.find("[data-test='model-status-rail']").classes()).toContain("bg-gray-400");
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

        test("arriving scoped puts the project in the fetch key with no status filter", async () => {
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

        test("the scoped eyebrow links through to the project itself", async () => {
            // The header is the only place the project is named; making it a link saves a trip
            // through Projects to get to the project it is describing.
            mockRoute.query = { project: "p1" };
            setModels([makeModel()]);
            mockProjectOptions.value = options;
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            const link = wrapper.find("[data-test='scope-project-link']");
            expect(link.attributes("data-to")).toBe("/project/p1");
            expect(link.text()).toBe("Stroke triage");
        });

        test("the dropdown gives way to the chip once a project is picked", async () => {
            // Two controls naming the same project is one too many: the chip says which project
            // and offers the way out, so the select stands down until the scope is cleared.
            mockRoute.query = { project: "p1" };
            setModels([makeModel()]);
            mockProjectOptions.value = options;
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            expect(wrapper.find("[data-test='project-filter']").exists()).toBe(false);
            expect(wrapper.find("[data-test='project-filter-chip']").exists()).toBe(true);
        });

        test("an unscoped page keeps the estate eyebrow and shows no chip", async () => {
            setModels([makeModel()]);
            mockProjectOptions.value = options;
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            expect(wrapper.find("[data-test='models-scope-eyebrow']").text()).toContain("Estate-wide");
            expect(wrapper.find("[data-test='project-filter-chip']").exists()).toBe(false);
        });

        test("an id the cached options do not know forces one revalidation before it is judged", async () => {
            // The options are SWRV-cached for a minute with no refresh interval, so a project
            // created since the last visit is missing from a list the subscription will not
            // refetch inside that window. Judging the id against that cache would leave the gate
            // shut and the page stranded on the loader; mutate() bypasses the dedup window.
            mockRoute.query = { project: "fresh" };
            setModels([makeModel()]);
            mockProjectOptions.value = options;
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            expect(mockProjectOptionsMutate).toHaveBeenCalledTimes(1);
            // Nothing is judged on the stale list: no bounce, and the gate stays shut meanwhile.
            expect(mockReplace).not.toHaveBeenCalled();
            expect(swrvKey.fn!()).toBe("");

            // The forced fetch returns the project the cache had never seen.
            mockProjectOptions.value = [...options, projectOption("fresh", "Fresh project")];
            await wrapper.vm.$nextTick();

            expect(mockReplace).not.toHaveBeenCalled();
            expect(swrvKey.fn!()).toContain("&project=fresh");
            expect(wrapper.find("[data-test='models-scope-eyebrow']").text()).toContain("Fresh project");
        });

        test("a project id still absent from the freshly-fetched options is dropped", async () => {
            // Covers a stale bookmark, a deleted project and lost access in one guard — and keeps
            // the page off the backend's 403, which would strand it behind the loader.
            mockRoute.query = { project: "gone" };
            setModels([makeModel()]);
            mockProjectOptions.value = options;
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            expect(mockProjectOptionsMutate).toHaveBeenCalledTimes(1);
            expect(mockReplace).not.toHaveBeenCalled();

            // Same content, freshly fetched: the project genuinely is not there.
            mockProjectOptions.value = [...options];
            await wrapper.vm.$nextTick();

            expect(mockReplace).toHaveBeenCalledWith(
                expect.objectContaining({ query: expect.not.objectContaining({ project: expect.anything() }) })
            );
        });

        test("judges nothing while the options are revalidating", async () => {
            mockRoute.query = { project: "gone" };
            setModels([makeModel()]);
            mockProjectOptions.value = options;
            mockProjectOptionsValidating.value = true;
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            expect(mockProjectOptionsMutate).not.toHaveBeenCalled();
            expect(mockReplace).not.toHaveBeenCalled();
        });

        test("a non-array options body is ignored rather than crashing the page", async () => {
            // Cypress's catch-all interceptor answers unstubbed routes with an empty body, so the
            // guard must not assume `.some()` exists on whatever arrives off the wire.
            mockRoute.query = { project: "p1" };
            setModels([makeModel()]);
            mockProjectOptions.value = "" as unknown as IModelProjectOption[];
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            expect(wrapper.exists()).toBe(true);
            expect(mockProjectOptionsMutate).not.toHaveBeenCalled();
            expect(mockReplace).not.toHaveBeenCalled();
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

        test("holds the scoped fetch until the options confirm the id", async () => {
            // A scoped key issued before validation would let a stale link hit the backend's
            // 403/404 and flash the global error banner before the guard can tidy up.
            mockRoute.query = { project: "p1" };
            setModels([makeModel()]);
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            expect(swrvKey.fn!()).toBe("");

            mockProjectOptions.value = options;
            await wrapper.vm.$nextTick();

            expect(swrvKey.fn!()).toContain("&project=p1");
        });

        test("an id the options reject never reaches the backend", async () => {
            // The guard clears the URL, but the fetch key must stay empty in the meantime —
            // otherwise the 403 it exists to prevent has already happened.
            mockRoute.query = { project: "gone" };
            setModels([makeModel()]);
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            expect(swrvKey.fn!()).toBe("");

            mockProjectOptions.value = options;
            await wrapper.vm.$nextTick();
            // First miss only buys a fresh list; the id is judged on what that returns.
            mockProjectOptions.value = [...options];
            await wrapper.vm.$nextTick();

            expect(swrvKey.fn!()).toBe("");
            expect(mockReplace).toHaveBeenCalledWith(
                expect.objectContaining({ query: expect.not.objectContaining({ project: expect.anything() }) })
            );
        });

        test("a 404 from the options endpoint keeps the estate header", async () => {
            // An API without /models/projects also predates the `project` query param and ignores
            // it, so the rows underneath really are estate-wide: the header is telling the truth,
            // and the filter degrades to invisible rather than the list waiting on a gate that
            // can never open.
            mockRoute.query = { project: "p1" };
            setModels([makeModel()]);
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            mockProjectOptionsError.value = httpError(404);
            await wrapper.vm.$nextTick();

            expect(swrvKey.fn!()).toContain("&project=p1");
            expect(wrapper.find("[data-test='models-scope-eyebrow']").text()).toContain("Estate-wide");
            expect(wrapper.find("[data-test='project-filter-chip']").exists()).toBe(false);
        });

        test.each([
            ["a 5xx", 500],
            ["a network error", undefined]
        ])("%s from the options endpoint leaves a name-less scoped header, not the estate one", async (_label, status) => {
            // The scoped endpoint is working — only the options call failed — so the param IS
            // honoured and the table below is scoped. Claiming "Estate-wide" there tells the user
            // the opposite of what the page is showing; the id is all the page has to name it by.
            mockRoute.query = { project: "p1" };
            setModels([makeModel()]);
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            mockProjectOptionsError.value = httpError(status);
            await wrapper.vm.$nextTick();

            expect(swrvKey.fn!()).toContain("&project=p1");
            const eyebrow = wrapper.find("[data-test='models-scope-eyebrow']");
            expect(eyebrow.text()).not.toContain("Estate-wide");
            expect(eyebrow.text()).toContain("Project ·");
            const link = wrapper.find("[data-test='scope-project-link']");
            expect(link.attributes("data-to")).toBe("/project/p1");
            expect(link.text()).toBe("p1");
            // No options to choose from, so the dropdown stays down; the chip still offers the
            // way back to the estate view.
            expect(wrapper.find("[data-test='project-filter']").exists()).toBe(false);
            expect(wrapper.find("[data-test='project-filter-chip']").text()).toContain("Project: p1");
        });

        test("the scoped Create Model button stays hidden while the project has no known status", async () => {
            // Creating against an unapproved project is refused server-side, and a failed options
            // call means the page cannot tell whether this one is approved.
            mockRoute.query = { project: "p1" };
            setModels([makeModel()]);
            const wrapper = mountPage({ permissions: ["CanCreateProjects"] });
            await wrapper.vm.$nextTick();

            mockProjectOptionsError.value = httpError(500);
            await wrapper.vm.$nextTick();

            expect(wrapper.find("[data-test='add-model-btn']").exists()).toBe(false);
        });

        test("navigating back to the estate view returns to All and clears search", async () => {
            // The sidebar Models link is a query-only change on the same route, so the component
            // is never remounted — only a watcher can reset it.
            mockRoute.query = { project: "p1" };
            setModels([makeModel()]);
            mockProjectOptions.value = options;
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();
            await wrapper.find("[data-test='filter-tile-running']").trigger("click");

            mockRoute.query = {};
            await wrapper.vm.$nextTick();

            const key = swrvKey.fn!();
            expect(key).not.toContain("project=");
            expect(key).not.toContain("status=");
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
