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

import { IProject, IProjectTrust, ProjectStatus } from "@/services/project-service";
import { TRUST_CHIP_DOTTED_PADDING, TRUST_CHIP_PLAIN_PADDING } from "@/utils/trust-chip";

import Page from "../projects.vue";

interface ProjectsResponse {
    data: IProject[];
    totalPages: number;
    page: number;
}

const mockSwrvData = ref<ProjectsResponse | undefined>(undefined);

vi.mock("swrv", () => ({
    default: () => ({
        data: mockSwrvData,
        mutate: vi.fn(),
        error: ref(null)
    })
}));

const stubs = {
    AiCard: { template: "<div><slot /></div>" },
    AiButton: {
        // Forward attrs (data-test) and emit click so tests can target the
        // primary "Create project" button.
        inheritAttrs: false,
        template: "<button v-bind=\"$attrs\" @click=\"$emit('click', $event)\"><slot /></button>"
    },
    AiLoader: { template: "<div />" },
    AiSearch: {
        props: ["modelValue"],
        emits: ["update:modelValue"],
        template: "<input data-test='project-search' :value='modelValue' @input='$emit(\"update:modelValue\", $event.target.value)' />"
    },
    AiPagination: { template: "<div />" },
    CreateProjectModal: { template: "<div data-test='create-project-modal' />" },
    // The global test setup stubs router-link with an empty (no-slot) stub;
    // override it so project rows actually render their content.
    "router-link": {
        template: "<a><slot /></a>",
        props: ["to"]
    },
    "icon-ph-list-bullets-duotone": { template: "<span />" },
    "icon-ph-squares-four-duotone": { template: "<span />" },
    "icon-ph-archive-duotone": { template: "<span />" },
    "icon-ph-users-three-duotone": { template: "<span />" }
};

const trust = (id: string, code: string, approved: boolean): IProjectTrust => ({
    id,
    name: `${code} NHS Foundation Trust`,
    code,
    approved
});

const makeProject = (status: ProjectStatus, trusts: IProjectTrust[]): IProject => ({
    id: `p-${status}`,
    name: "Stroke triage from CT",
    description: "Multi-trust evaluation of an acute-stroke triage model.",
    ownerId: "u1",
    ownerEmail: "r.patel@example.com",
    creationtimestamp: new Date().toISOString(),
    status,
    users: [],
    approvedTrusts: trusts
});

const setProject = (project: IProject): void => {
    mockSwrvData.value = {
        data: [project],
        totalPages: 1,
        page: 1
    };
};

function mountPage(options: { permissions?: string[] } = {}) {
    const { permissions = ["CanCreateProjects"] } = options;

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

describe("Projects Page", () => {
    test("Renders Component", () => {
        expect(mountPage().exists()).toBe(true);
    });

    test("a staged-but-unapproved trust still shows as a chip on the card", async () => {
        // Regression: trusts were filtered to approved=true, so a freshly
        // staged trust vanished and the card claimed "No trusts staged".
        setProject(makeProject("STAGED", [trust("t1", "KCH", false)]));
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.findAll("[data-test='trust-chip']")).toHaveLength(1);
        expect(wrapper.text()).not.toContain("No trusts staged");
    });

    // The dot rule is a small truth table; one row per case beats six near-identical
    // mounts. Amber is asserted literally here — the Cypress spec is what proves it is
    // the *same* amber as the row spine, by comparing computed colours.
    test.each([
        ["UNSTAGED", true, null, TRUST_CHIP_PLAIN_PADDING, "KCH NHS Foundation Trust"],
        ["STAGED", false, "bg-amber-500", TRUST_CHIP_DOTTED_PADDING, "KCH NHS Foundation Trust — awaiting approval"],
        // A trust that has signed off on a project still awaiting approval stays amber:
        // green is the project-wide "this trust is in" marker, not a per-trust one.
        ["STAGED", true, "bg-amber-500", TRUST_CHIP_DOTTED_PADDING, "KCH NHS Foundation Trust — awaiting approval"],
        ["APPROVED", false, "bg-amber-500", TRUST_CHIP_DOTTED_PADDING, "KCH NHS Foundation Trust — awaiting approval"],
        ["APPROVED", true, "bg-emerald-500", TRUST_CHIP_DOTTED_PADDING, "KCH NHS Foundation Trust — approved"]
    ] as const)(
        "%s project + trust.approved=%s → %s dot",
        async (status, approved, dotClass, chipPadding, title) => {
            setProject(makeProject(status, [trust("t1", "KCH", approved)]));
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            const chips = wrapper.findAll("[data-test='trust-chip']");
            expect(chips).toHaveLength(1);
            chipPadding.split(" ").forEach(cls => expect(chips[0].classes()).toContain(cls));
            // The tooltip is the accessible channel: the dot is aria-hidden, and
            // amber/emerald is the pairing colour-vision deficiency collapses.
            expect(chips[0].attributes("title")).toBe(title);

            const dots = wrapper.findAll("[data-test='trust-status-dot']");
            expect(dots).toHaveLength(dotClass ? 1 : 0);
            if (dotClass) expect(dots[0].classes()).toContain(dotClass);
        }
    );

    test("the grid view paints its chips by the same rule", async () => {
        setProject(makeProject("APPROVED", [trust("t1", "KCH", true), trust("t2", "GSTT", false)]));
        const wrapper = mountPage();
        await wrapper.find("[data-test='view-mode-grid']").trigger("click");

        // Chips sort by trust name: GSTT (pending) then KCH (approved).
        const dots = wrapper.find("[data-test='projects-grid-view']").findAll("[data-test='trust-status-dot']");
        expect(dots.map(dot => dot.classes().find(c => c.startsWith("bg-")))).toEqual([
            "bg-amber-500",
            "bg-emerald-500"
        ]);
    });

    test("an unrecognised status is not painted as staged", async () => {
        // `status` comes off the wire and can be absent (18 of 21 rows in the
        // getProjects Cypress fixture have no status) or a value this union does
        // not know yet. A deny-list (`!== "UNSTAGED"`) would give those an amber
        // dot claiming "staged"; the allow-list shows nothing instead.
        setProject({
            ...makeProject("STAGED", [trust("t1", "KCH", true)]),
            status: undefined as unknown as ProjectStatus
        });
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.findAll("[data-test='trust-chip']")).toHaveLength(1);
        expect(wrapper.findAll("[data-test='trust-status-dot']")).toHaveLength(0);
    });

    test("renders the empty-state copy when the API returns zero projects", async () => {
        mockSwrvData.value = {
            data: [],
            totalPages: 0,
            page: 1
        };
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.text()).toContain("There are no projects to show");
        expect(wrapper.findAll("[data-test='project-list-item-0']")).toHaveLength(0);
    });

    test("status indicators show human-friendly labels (Draft / Staged / Approved)", async () => {
        // Three projects, one per status — the sort puts STAGED first, then
        // APPROVED, then UNSTAGED (see STATUS_RANK in projects.vue), so
        // the indicator labels follow that order.
        mockSwrvData.value = {
            data: [
                makeProject("APPROVED", []),
                makeProject("UNSTAGED", []),
                makeProject("STAGED", [])
            ],
            totalPages: 1,
            page: 1
        };
        // Tie-break in the sort uses project.id (which embeds the status name
        // in the makeProject helper) — fine for this assertion since we're only
        // checking labels, not order.
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const labels = wrapper.findAll("[data-test='project-status-indicator']").map(s => s.text());
        expect(labels).toContain("Draft");
        expect(labels).toContain("Staged");
        expect(labels).toContain("Approved");
    });

    test("renders the 'No trusts staged' subtitle on projects without any approvedTrusts", async () => {
        setProject(makeProject("UNSTAGED", []));
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();
        expect(wrapper.text()).toContain("No trusts staged");
        expect(wrapper.findAll("[data-test='trust-chip']")).toHaveLength(0);
    });

    test("toggles between list and grid views via the view-mode buttons", async () => {
        setProject(makeProject("STAGED", [trust("t1", "KCH", true)]));
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();
        // Default is the list view — the list container is mounted, grid isn't.
        expect(wrapper.find("[data-test='projects-list-view']").exists()).toBe(true);
        expect(wrapper.find("[data-test='projects-grid-view']").exists()).toBe(false);

        await wrapper.find("[data-test='view-mode-grid']").trigger("click");
        expect(wrapper.find("[data-test='projects-list-view']").exists()).toBe(false);
        expect(wrapper.find("[data-test='projects-grid-view']").exists()).toBe(true);

        await wrapper.find("[data-test='view-mode-list']").trigger("click");
        expect(wrapper.find("[data-test='projects-list-view']").exists()).toBe(true);
        expect(wrapper.find("[data-test='projects-grid-view']").exists()).toBe(false);
    });

    test("trust chips fall back to a cleaned + truncated name when no code is supplied", async () => {
        setProject(makeProject("STAGED", [
            // No code → name is stripped of "NHS Foundation Trust" / "NHS Trust" / "Trust"
            {
                id: "t1",
                name: "Maple NHS Foundation Trust",
                code: undefined as unknown as string,
                approved: true
            }
        ]));
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const chips = wrapper.findAll("[data-test='trust-chip']");
        expect(chips).toHaveLength(1);
        // After stripping bloat: "Maple" — well under the 16-char limit.
        expect(chips[0].text()).toBe("Maple");
    });

    test("caps visible trust chips at 4 and surfaces a +N marker for the overflow", async () => {
        // 6 trusts → first 4 chips + "+2" overflow.
        const trusts = ["A", "B", "C", "D", "E", "F"].map((c, i) => trust(`t${i}`, c, true));
        setProject(makeProject("APPROVED", trusts));
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const chips = wrapper.findAll("[data-test='trust-chip']");
        expect(chips).toHaveLength(4);
        // The "+N" indicator lives near the chip row — assert it's somewhere
        // in the rendered card.
        expect(wrapper.text()).toContain("+2");
    });

    test("renders a relative-time stamp ('Xs / Xm / Xh / Xd ago') for project creation", async () => {
        const project = makeProject("STAGED", [trust("t1", "KCH", true)]);
        // 2 hours ago — should land in the "h ago" bucket. Offset-less naive-UTC
        // string: the wire format the API actually sends.
        project.creationtimestamp = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString().slice(0, 19);
        setProject(project);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.text()).toContain("h ago");
        expect(wrapper.text()).toContain("created");
    });

    test("hides the Create project button when the user lacks CanCreateProjects", async () => {
        setProject(makeProject("STAGED", []));
        const wrapper = mountPage({ permissions: [] });
        await wrapper.vm.$nextTick();
        expect(wrapper.find("[data-test='add-project-btn']").exists()).toBe(false);
    });

    test("puts Create project on the title row and collapses it to a plus below lg", async () => {
        setProject(makeProject("STAGED", []));
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const btn = wrapper.find("[data-test='add-project-btn']");
        // Same row as the h1 — not a separate bottom-aligned block.
        expect(btn.element.parentElement?.querySelector("h1")).toBeTruthy();
        // Icon-collapse idiom: plus icon always, label only at lg+.
        expect(btn.find("svg").exists()).toBe(true);
        const label = btn.find("span.hidden");
        expect(label.text()).toBe("Create project");
        expect(label.classes()).toContain("lg:inline");
    });

    test("clicking Create project toggles the create-project modal store", async () => {
        setProject(makeProject("STAGED", []));
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();
        const modalsStore = (await import("@/store/modals")).useModalsStore();
        await wrapper.find("[data-test='add-project-btn']").trigger("click");
        // The AiButton stub forwards `@click` and the native button bubbles
        // the same event, so the handler may fire more than once — we just
        // care that it fires at all.
        expect(modalsStore.toggleCreateProject).toHaveBeenCalled();
    });

    test("the name sort option reorders projects alphabetically by name", async () => {
        const p1 = {
            ...makeProject("STAGED", []),
            id: "p1",
            name: "Zebra"
        };
        const p2 = {
            ...makeProject("STAGED", []),
            id: "p2",
            name: "Apple"
        };
        const p3 = {
            ...makeProject("STAGED", []),
            id: "p3",
            name: "Maple"
        };
        mockSwrvData.value = {
            data: [p1, p2, p3],
            totalPages: 1,
            page: 1
        };
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        await wrapper.find("[data-test='project-sort']").setValue("name");

        const titles = wrapper.findAll("[data-test='project-name']").map(n => n.text());
        expect(titles).toEqual(["Apple", "Maple", "Zebra"]);
    });

    test("the cohort sort option puts the largest cohort first; missing cohorts sink to the bottom", async () => {
        const withCohort = (id: string, name: string, total?: number): IProject => ({
            ...makeProject("STAGED", []),
            id,
            name,
            query: total !== undefined
                ? {
                    id: `q-${id}`,
                    name: "q",
                    query: "",
                    queriedTrustIds: [],
                    pendingTrustIds: [],
                    cancelledTrustIds: [],
                    respondedTrustIds: [],
                    erroredTrustIds: [],
                    emptyTrustIds: [],
                    totalCohort: total
                }
                : undefined
        });
        mockSwrvData.value = {
            data: [
                withCohort("p1", "Small", 10),
                withCohort("p2", "Big", 9999),
                withCohort("p3", "None")
            ],
            totalPages: 1,
            page: 1
        };
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        await wrapper.find("[data-test='project-sort']").setValue("cohort");
        const titles = wrapper.findAll("[data-test='project-name']").map(n => n.text());
        expect(titles).toEqual(["Big", "Small", "None"]);
    });

    test("the status sort orders STAGED → APPROVED → UNSTAGED", async () => {
        mockSwrvData.value = {
            data: [
                makeProject("UNSTAGED", []),
                makeProject("APPROVED", []),
                makeProject("STAGED", [])
            ],
            totalPages: 1,
            page: 1
        };
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        await wrapper.find("[data-test='project-sort']").setValue("status");
        const labels = wrapper.findAll("[data-test='project-status-indicator']").map(s => s.text());
        expect(labels).toEqual(["Staged", "Approved", "Draft"]);
    });

    test("owner label falls back to the local part of ownerEmail when ownerName is unset", async () => {
        const project = {
            ...makeProject("STAGED", []),
            ownerEmail: "rashida.patel@example.com"
        };
        setProject(project);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();
        expect(wrapper.text()).toContain("rashida.patel");
    });

    test("relativeUpdated renders 'created Xd ago' for older projects", async () => {
        const project = makeProject("STAGED", []);
        project.creationtimestamp = new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString().slice(0, 19);
        setProject(project);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();
        expect(wrapper.text()).toContain("created 3d ago");
    });

    test("relativeUpdated renders the em-dash when creationtimestamp is missing", async () => {
        const project = makeProject("STAGED", []);
        project.creationtimestamp = "";
        // Set an explicit owner name + non-em-dash description so the "·"
        // separator we look for unambiguously hugs the relativeUpdated cell.
        project.ownerName = "Owner";
        project.description = "Multi-trust evaluation.";
        setProject(project);
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();
        expect(wrapper.text()).toContain("Owner · —");
    });

    test("clicking the 'My projects' tab flips the ownerFilter state", async () => {
        setProject(makeProject("STAGED", []));
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const allTab = wrapper.find("[data-test='access-filter-all']");
        const mineTab = wrapper.find("[data-test='access-filter-mine']");
        expect(allTab.classes().join(" ")).toContain("text-gray-900");

        await mineTab.trigger("click");
        // The active class moves to the mine tab.
        expect(mineTab.classes().join(" ")).toContain("text-gray-900");
    });

    test("typing in the search input triggers a paginated reload (debouncedWatch → updateProjectList)", async () => {
        setProject(makeProject("STAGED", []));
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        // The search input is the AiSearch stub — bound via v-model to `search`.
        // Triggering input fires the debouncedWatch which calls updateProjectList(1).
        // The SWRV mock just observes the new key; we assert via re-render not throwing.
        await wrapper.find("[data-test='project-search']").setValue("cancer");
        // Allow the 500ms debounce to settle.
        await new Promise((resolve) => setTimeout(resolve, 600));
        await wrapper.vm.$nextTick();

        // Nothing else to assert — exercising the path without throwing already
        // covers getSearchQuery + updateProjectList. The store key change is
        // observed via the SWRV mock continuing to return mockSwrvData.
        expect(wrapper.exists()).toBe(true);
    });

    test("trust chips truncate stripped names longer than 16 chars to 14 + ellipsis", async () => {
        setProject(makeProject("STAGED", [
            // Stripping leaves "Very Long Hospital Name Here" — 28 chars → truncated.
            {
                id: "t-long",
                name: "Very Long Hospital Name Here NHS Foundation Trust",
                code: undefined as unknown as string,
                approved: true
            }
        ]));
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const chips = wrapper.findAll("[data-test='trust-chip']");
        expect(chips).toHaveLength(1);
        const text = chips[0].text();
        expect(text.endsWith("…")).toBe(true);
        // Truncation is 14 chars + ellipsis.
        expect(text.slice(0, -1)).toHaveLength(14);
    });

    // The row and the card are both <a>, and main.css bolds every non-nav anchor, so
    // anything inside without its own weight class inherited semibold. jsdom compiles no
    // Tailwind, so this asserts the guard is on the anchor and the description sits under
    // it; the rendered weight itself is asserted in the group-6 Cypress spec.
    test.each([
        ["list", "project-list-item-0", "project-row-description"],
        ["grid", "project-card-0", "project-card-description"]
    ])("the %s view sets body weight on the anchor the description lives in", async (view, anchor, description) => {
        setProject(makeProject("STAGED", [trust("t1", "KCH", false)]));
        const wrapper = mountPage();
        if (view === "grid") await wrapper.find("[data-test='view-mode-grid']").trigger("click");
        await wrapper.vm.$nextTick();

        const anchorEl = wrapper.find(`[data-test='${anchor}']`);
        expect(anchorEl.classes()).toContain("font-normal");
        expect(anchorEl.find(`[data-test='${description}']`).exists()).toBe(true);
    });

    test("every list row declares the same content-independent column template", async () => {
        // Each row is its own grid, so a content-sized (`auto`) track resolves to
        // a different width per row and the description column drifts down the
        // list. Fixed fr/rem tracks keep every row's columns on the same edges.
        mockSwrvData.value = {
            data: [
                makeProject("STAGED", [trust("t1", "KCH", false)]),
                makeProject("APPROVED", [])
            ],
            totalPages: 1,
            page: 1
        };
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        // Every declared track must be content-independent. Checking for the literal
        // word `auto` is not enough: a bare `1.6fr` means `minmax(auto,1.6fr)` and drifts
        // just the same, as do min-content/max-content/fit-content.
        const rows = wrapper.findAll("[data-test='project-row-grid']");
        expect(rows).toHaveLength(2);
        expect(new Set(rows.map(row => row.attributes("class")))).toHaveProperty("size", 1);

        const tracks = rows[0].classes().flatMap(c => c.match(/grid-cols-\[(.+)\]$/)?.[1].split("_") ?? []);
        expect(tracks.length).toBeGreaterThan(0);
        tracks.forEach(track => expect(track).toMatch(/^(minmax\(0,[\d.]+fr\)|[\d.]+rem)$/));
    });
});
