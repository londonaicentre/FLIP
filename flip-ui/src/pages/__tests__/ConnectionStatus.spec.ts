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
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";

import { ITrustResponse } from "@/services/trust-service";

import ConnectionStatus from "../ConnectionStatus.vue";

// Capture the (key, fetcher, options) the page wires SWRV with, so a test can
// assert it polls the authenticated, consolidated /trust endpoint (under its own
// SWRV cache key) rather than the admin-only /admin/trusts (regression for #557).
const { swrvCalls } = vi.hoisted(() => ({ swrvCalls: [] as unknown[][] }));

const mockSwrvData = ref<ITrustResponse[] | undefined>(undefined);

vi.mock("swrv", () => ({
    default: (...args: unknown[]) => {
        swrvCalls.push(args);

        return {
            data: mockSwrvData,
            mutate: vi.fn(),
            error: ref(null)
        };
    }
}));

const stubs = {
    AiCard: { template: "<div><slot /></div>" },
    AiButton: {
        // Forward attrs (including data-test) so tests can target this stubbed button.
        template: "<button v-bind=\"$attrs\"><slot /></button>",
        inheritAttrs: false
    },
    AiLoader: { template: "<div data-test=\"ai-loader\" />" },
    AddTrustModal: {
        name: "AddTrustModal",
        props: ["dialog"],
        emits: ["close-modal", "on-success"],
        template: "<div data-test='add-trust-modal' :data-dialog='dialog' />"
    },
    TrustKitModal: {
        name: "TrustKitModal",
        props: ["dialog", "trust"],
        emits: ["close-modal"],
        template: "<div data-test='trust-kit-modal' :data-dialog='dialog' />"
    },
    // Stubbed: the shared swrv mock returns trust fixtures, which the partial
    // (which expects IFLStatus shape) would otherwise misinterpret.
    FLNetsCard: { template: "<div />" },
    "icon-ph-list-bullets-duotone": { template: "<span />" },
    "icon-ph-share-network-duotone": { template: "<span />" }
};

const now = Date.now();
const seconds = (n: number) => new Date(now - n * 1000).toISOString();

const fixture: ITrustResponse[] = [
    {
        id: "t1",
        name: "Zebra NHS Trust",
        code: "ZNT",
        region: "London",
        last_heartbeat: seconds(10),
        project_count: 1
    },
    {
        id: "t2",
        name: "Acme NHS Trust",
        code: "ANT",
        region: "South West",
        last_heartbeat: null,
        project_count: 7
    },
    {
        id: "t3",
        name: "Maple NHS Trust",
        code: "MNT",
        region: "North East",
        last_heartbeat: seconds(120),
        project_count: 3
    }
];

interface MountOptions {
    permissions?: string[];
}

function mountPage({ permissions = ["CanAccessAdminPanel"] }: MountOptions = {}) {
    return mount(ConnectionStatus, {
        global: {
            plugins: [createTestingPinia({
                createSpy: vi.fn,
                stubActions: false,
                initialState: { auth: { user: { permissions } } }
            })],
            stubs
        }
    });
}

// The TRUST column shows the name prominently with the code in a small span
// below (data-test="trust-code"). These sort tests verify row ORDER by code,
// which mirrors name order in the fixture, so read the code span.
const codesInOrder = (wrapper: ReturnType<typeof mountPage>): string[] =>
    wrapper.findAll("[data-test='trust-row']").map(r => {
        const code = r.find("[data-test='trust-code']");

        return code.exists() ? code.text() : "";
    });

beforeEach(() => {
    mockSwrvData.value = undefined;
    swrvCalls.length = 0;
});

describe("ConnectionStatus", () => {
    it("defaults to alphabetical sort by trust name", async () => {
        mockSwrvData.value = fixture;
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();
        expect(codesInOrder(wrapper)).toEqual(["ANT", "MNT", "ZNT"]);
    });

    it("toggles to descending on a second click of the same column", async () => {
        mockSwrvData.value = fixture;
        const wrapper = mountPage();
        const trustHeader = wrapper.find("[data-test='sort-header-name']");
        await trustHeader.trigger("click");
        expect(codesInOrder(wrapper)).toEqual(["ZNT", "MNT", "ANT"]);
        await trustHeader.trigger("click");
        expect(codesInOrder(wrapper)).toEqual(["ANT", "MNT", "ZNT"]);
    });

    it("sorts by severity (offline first) when the Status header is clicked", async () => {
        mockSwrvData.value = fixture;
        const wrapper = mountPage();
        await wrapper.find("[data-test='sort-header-severity']").trigger("click");
        // Acme has no heartbeat → offline; Maple is degraded (2 min old); Zebra is online.
        expect(codesInOrder(wrapper)).toEqual(["ANT", "MNT", "ZNT"]);
    });

    it("sorts by project count, ascending then descending, on the Projects header", async () => {
        // Fixture project counts: Zebra=1, Maple=3, Acme=7
        mockSwrvData.value = fixture;
        const wrapper = mountPage();
        const projectsHeader = wrapper.find("[data-test='sort-header-projects']");
        await projectsHeader.trigger("click");
        expect(codesInOrder(wrapper)).toEqual(["ZNT", "MNT", "ANT"]);
        await projectsHeader.trigger("click");
        expect(codesInOrder(wrapper)).toEqual(["ANT", "MNT", "ZNT"]);
    });

    it("shows an up arrow on the active ascending column and a down arrow on descending", async () => {
        mockSwrvData.value = fixture;
        const wrapper = mountPage();
        const nameHeader = wrapper.find("[data-test='sort-header-name']");
        expect(nameHeader.text()).toContain("↑");
        await nameHeader.trigger("click");
        expect(nameHeader.text()).toContain("↓");
    });

    it("counts trusts in the subtitle header (3 → 'trusts', 1 → 'trust')", async () => {
        mockSwrvData.value = fixture;
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();
        // Header copy is "Federation · 3 trusts" — the loaded fixture has three rows.
        expect(wrapper.text()).toContain("3 trusts");

        mockSwrvData.value = [fixture[0]];
        await wrapper.vm.$nextTick();
        expect(wrapper.text()).toContain("1 trust");
        expect(wrapper.text()).not.toContain("1 trusts");
    });

    it("hides the Add Trust button for non-admins", async () => {
        mockSwrvData.value = fixture;
        const wrapper = mountPage({ permissions: [] });
        await wrapper.vm.$nextTick();
        expect(wrapper.find("[data-test='add-trust-btn']").exists()).toBe(false);
    });

    it("sources statuses from the authenticated /trust endpoint, not admin-only /admin/trusts", () => {
        // #557: a non-admin polling /admin/trusts gets a 403 → perpetual spinner.
        // The page must use the non-admin endpoint instead — post-#609 the
        // consolidated GET /trust, fetched here under a distinct SWRV cache key
        // so its 15s poll stays independent of the app-wide /trust bootstrap.
        mockSwrvData.value = fixture;
        mountPage({ permissions: [] });
        expect(swrvCalls[0][0]).toBe("trust-connection-status");
    });

    it("lets a non-admin (Researcher / Viewer) load trust statuses with no Add Trust control", async () => {
        // Acceptance criteria for #557: a non-admin sees the statuses populate
        // (no perpetual loader) while the admin-only Add Trust control stays hidden.
        mockSwrvData.value = fixture;
        const wrapper = mountPage({ permissions: [] });
        await wrapper.vm.$nextTick();
        expect(wrapper.find("[data-test='ai-loader']").exists()).toBe(false);
        expect(wrapper.findAll("[data-test='trust-row']").length).toBe(fixture.length);
        expect(wrapper.find("[data-test='add-trust-btn']").exists()).toBe(false);
    });

    it("shows the Add Trust button for admins", async () => {
        mockSwrvData.value = fixture;
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();
        expect(wrapper.find("[data-test='add-trust-btn']").exists()).toBe(true);
    });

    it("renders the trust name prominently with the code beneath it (code hidden when absent)", async () => {
        mockSwrvData.value = [
            {
                ...fixture[0],
                code: undefined as unknown as string
            }, // codeless trust → name only, no code line
            fixture[1]
        ];
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();
        const rows = wrapper.findAll("[data-test='trust-row']");
        // Sorted alphabetically by name: "Acme NHS Trust" (ANT) before "Zebra NHS Trust" (no code).
        expect(rows[0].find("[data-test='trust-name']").text()).toBe("Acme NHS Trust");
        expect(rows[0].find("[data-test='trust-code']").text()).toBe("ANT");
        expect(rows[1].find("[data-test='trust-name']").text()).toBe("Zebra NHS Trust");
        expect(rows[1].find("[data-test='trust-code']").exists()).toBe(false);
    });

    it("flags an offline trust (null heartbeat) and surfaces it via the red row background", async () => {
        // Acme (second fixture entry) has last_heartbeat: null → state "offline".
        mockSwrvData.value = [fixture[1]];
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();
        const row = wrapper.find("[data-test='trust-row']");
        // The offline-red bg pulls the row out of the default zebra striping.
        expect(row.classes().some(c => c.startsWith("bg-red"))).toBe(true);
        // And the heartbeat cell renders the offline marker ("Never" / "—" / blank
        // is implementation-defined; the contract is: it does NOT show a relative
        // time like "ago"). We assert the negative.
        expect(wrapper.find("[data-test='trust-heartbeat']").text()).not.toContain("ago");
    });

    it("leaves non-offline trust rows on the card surface, matching the mobile rows", async () => {
        // Zebra (first fixture entry) is online — no red tint applies, and no
        // dark-surface either: rows sit transparent on the card's dark canvas
        // exactly like the stacked mobile rows.
        mockSwrvData.value = [fixture[0]];
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();
        const row = wrapper.find("[data-test='trust-row']");
        expect(row.classes().some(c => c.startsWith("bg-red"))).toBe(false);
        expect(row.classes()).not.toContain("dark:bg-dark-surface");

        // main.css paints every table's tbody dark:bg-gray-800 with its own
        // ring/shadow and gray-700 dividers — this table must override those
        // so dark mode shows the card canvas through, like the mobile list.
        const tbody = wrapper.find("tbody");
        expect(tbody.classes()).toContain("dark:bg-transparent");
        expect(tbody.classes()).toContain("dark:divide-dark-border");
        const table = wrapper.find("table");
        expect(table.classes()).toContain("ring-0");
        expect(table.classes()).toContain("shadow-none");
    });

    it("renders the radial topology on the plain card surface with dark-visible labels", async () => {
        mockSwrvData.value = fixture;
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();
        await wrapper.find("[data-test='view-toggle-radial']").trigger("click");

        // The bespoke gradient panel (off-token blue-grey in dark mode) is gone —
        // the topology sits directly on the card surface like every other card.
        expect(wrapper.find(".connection-topology-card").exists()).toBe(false);

        // SVG labels swap fills per mode instead of hard-coded light-mode hexes.
        const svg = wrapper.find("[data-test='connection-radial-svg']");
        const names = svg.findAll("text.fill-gray-700");
        expect(names.length).toBeGreaterThan(0);
        for (const name of names) {
            expect(name.classes()).toContain("dark:fill-gray-100");
        }
        const counts = svg.findAll("text.fill-gray-500");
        expect(counts.length).toBeGreaterThan(0);
        for (const count of counts) {
            expect(count.classes()).toContain("dark:fill-gray-300");
        }
    });

    it("toggles to the radial topology view when its tab is clicked", async () => {
        mockSwrvData.value = fixture;
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();
        // Table view is the default — the list has rows, the radial SVG isn't mounted.
        expect(wrapper.findAll("[data-test='trust-row']").length).toBe(3);
        expect(wrapper.find("[data-test='connection-radial-svg']").exists()).toBe(false);

        await wrapper.find("[data-test='view-toggle-radial']").trigger("click");
        // After toggling: the rows disappear, the SVG is mounted.
        expect(wrapper.find("[data-test='connection-radial-svg']").exists()).toBe(true);
        expect(wrapper.findAll("[data-test='trust-row']").length).toBe(0);
    });

    it("flips the aria-selected hints on the view tabs", async () => {
        mockSwrvData.value = fixture;
        const wrapper = mountPage();
        const listTab = wrapper.find("[data-test='view-toggle-list']");
        const radialTab = wrapper.find("[data-test='view-toggle-radial']");
        expect(listTab.attributes("aria-selected")).toBe("true");
        expect(radialTab.attributes("aria-selected")).toBe("false");

        await radialTab.trigger("click");
        expect(listTab.attributes("aria-selected")).toBe("false");
        expect(radialTab.attributes("aria-selected")).toBe("true");
    });

    it("sorts by region alphabetically and toggles direction on repeat click", async () => {
        mockSwrvData.value = fixture;
        const wrapper = mountPage();
        const regionHeader = wrapper.find("[data-test='sort-header-region']");
        await regionHeader.trigger("click");
        // Regions: London (ZNT), North East (MNT), South West (ANT).
        expect(codesInOrder(wrapper)).toEqual(["ZNT", "MNT", "ANT"]);
        await regionHeader.trigger("click");
        expect(codesInOrder(wrapper)).toEqual(["ANT", "MNT", "ZNT"]);
    });

    it("sorts by heartbeat freshness, pushing the never-heartbeat trust to the end", async () => {
        mockSwrvData.value = fixture;
        const wrapper = mountPage();
        // Freshness: Zebra 10s, Maple 120s, Acme never (Infinity).
        await wrapper.find("[data-test='sort-header-heartbeat']").trigger("click");
        expect(codesInOrder(wrapper)).toEqual(["ZNT", "MNT", "ANT"]);
    });

    it("renders hour and day buckets in the heartbeat column", async () => {
        const HOUR = 60 * 60;
        const DAY = 24 * HOUR;
        mockSwrvData.value = [
            {
                ...fixture[0],
                last_heartbeat: seconds(2 * HOUR)
            },
            {
                ...fixture[2],
                last_heartbeat: seconds(3 * DAY)
            }
        ];
        const wrapper = mountPage();
        const heartbeats = wrapper.findAll("[data-test='trust-heartbeat']").map(h => h.text());
        expect(heartbeats.some(t => t.includes("h ago"))).toBe(true);
        expect(heartbeats.some(t => t.includes("d ago"))).toBe(true);
    });

    it("opens the Add Trust modal when an admin clicks the button", async () => {
        mockSwrvData.value = fixture;
        const wrapper = mountPage();
        const modal = wrapper.findComponent({ name: "AddTrustModal" });
        // The stub forwards its `dialog` prop verbatim — start closed.
        expect(modal.props("dialog")).toBe(false);

        await wrapper.find("[data-test='add-trust-btn']").trigger("click");
        expect(modal.props("dialog")).toBe(true);
    });

    it("closes AddTrustModal and opens TrustKitModal when a trust is created", async () => {
        mockSwrvData.value = fixture;
        const wrapper = mountPage();

        await wrapper.find("[data-test='add-trust-btn']").trigger("click");
        const addModal = wrapper.findComponent({ name: "AddTrustModal" });
        const kitModal = wrapper.findComponent({ name: "TrustKitModal" });
        expect(addModal.props("dialog")).toBe(true);

        await addModal.vm.$emit("on-success", {
            id: "new-id",
            name: "New Trust"
        });
        expect(addModal.props("dialog")).toBe(false);
        expect(kitModal.props("dialog")).toBe(true);

        await kitModal.vm.$emit("close-modal");
        expect(kitModal.props("dialog")).toBe(false);
    });

    it("returns to the table view when the List tab is clicked after switching to radial", async () => {
        mockSwrvData.value = fixture;
        const wrapper = mountPage();
        await wrapper.find("[data-test='view-toggle-radial']").trigger("click");
        expect(wrapper.find("[data-test='connection-radial-svg']").exists()).toBe(true);

        await wrapper.find("[data-test='view-toggle-list']").trigger("click");
        // Back to the table: rows are mounted again and the radial SVG is gone.
        expect(wrapper.findAll("[data-test='trust-row']").length).toBe(3);
        expect(wrapper.find("[data-test='connection-radial-svg']").exists()).toBe(false);
    });

    it("closes the Add Trust modal when AddTrustModal emits close-modal (dismiss without creating)", async () => {
        mockSwrvData.value = fixture;
        const wrapper = mountPage();

        await wrapper.find("[data-test='add-trust-btn']").trigger("click");
        const addModal = wrapper.findComponent({ name: "AddTrustModal" });
        expect(addModal.props("dialog")).toBe(true);

        await addModal.vm.$emit("close-modal");
        expect(addModal.props("dialog")).toBe(false);
    });

    it("sets hoverTrustId on radial mouseenter and clears it on mouseleave", async () => {
        mockSwrvData.value = fixture;
        const wrapper = mountPage();
        await wrapper.find("[data-test='view-toggle-radial']").trigger("click");

        const detail = () => wrapper.find("[data-test='radial-hover-detail']");
        expect(detail().exists()).toBe(false);

        const nodes = wrapper.find("[data-test='connection-radial-svg']").findAll("g[style*='cursor: pointer']");
        expect(nodes.length).toBe(fixture.length);
        await nodes[0].trigger("mouseenter");
        expect(detail().exists()).toBe(true);

        await nodes[0].trigger("mouseleave");
        expect(detail().exists()).toBe(false);
    });

    describe("mobile stacked trust rows (design 3a)", () => {
        const mobileCodes = (wrapper: ReturnType<typeof mountPage>): string[] =>
            wrapper.findAll("[data-test='trust-row-mobile']").map(r => {
                const code = r.find("[data-test='trust-code-mobile']");

                return code.exists() ? code.text() : "";
            });

        it("renders a stacked row per trust below sm and hides the table there", async () => {
            mockSwrvData.value = fixture;
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            const list = wrapper.find("[data-test='trusts-stacked-list']");
            expect(list.classes()).toContain("sm:hidden");
            expect(wrapper.findAll("[data-test='trust-row-mobile']")).toHaveLength(fixture.length);

            const tableWrap = wrapper.find("table").element.parentElement;
            expect(tableWrap?.className).toContain("hidden");
            expect(tableWrap?.className).toContain("sm:block");
        });

        it("stacks name, code, pill, meta line and sparkline into two lines", async () => {
            mockSwrvData.value = [fixture[0]]; // Zebra: online, London, 1 project
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            const row = wrapper.find("[data-test='trust-row-mobile']");
            expect(row.text()).toContain("Zebra NHS Trust");
            expect(row.find("[data-test='trust-code-mobile']").text()).toBe("ZNT");
            expect(row.text()).toContain("Online");
            expect(row.text()).toContain("London");
            expect(row.text()).toContain("ago");
            expect(row.text()).toContain("1 project");
            expect(row.find("svg polyline").exists()).toBe(true);
        });

        it("tints the offline stacked row and its heartbeat red", async () => {
            mockSwrvData.value = [fixture[1]]; // Acme: null heartbeat → offline
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            const row = wrapper.find("[data-test='trust-row-mobile']");
            expect(row.classes().some(c => c.startsWith("bg-red"))).toBe(true);
            expect(row.find("[data-test='trust-heartbeat-mobile']").classes().join(" ")).toContain("text-red-600");
        });

        it("sorts from the mobile sort menu and toggles direction on repeat", async () => {
            mockSwrvData.value = fixture;
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            expect(wrapper.find("[data-test='mobile-sort-btn']").text()).toContain("Sort: Name ↑");

            // Fixture project counts: Zebra=1, Maple=3, Acme=7.
            await wrapper.find("[data-test='mobile-sort-btn']").trigger("click");
            await wrapper.find("[data-test='mobile-sort-projects']").trigger("click");
            expect(mobileCodes(wrapper)).toEqual(["ZNT", "MNT", "ANT"]);
            expect(wrapper.find("[data-test='mobile-sort-btn']").text()).toContain("Sort: Projects ↑");

            await wrapper.find("[data-test='mobile-sort-btn']").trigger("click");
            await wrapper.find("[data-test='mobile-sort-projects']").trigger("click");
            expect(mobileCodes(wrapper)).toEqual(["ANT", "MNT", "ZNT"]);
            expect(wrapper.find("[data-test='mobile-sort-btn']").text()).toContain("Sort: Projects ↓");
        });

        it("applies the active filter tile to the stacked rows too", async () => {
            mockSwrvData.value = fixture;
            const wrapper = mountPage();

            await wrapper.find("[data-test='filter-tile-offline']").trigger("click");
            expect(mobileCodes(wrapper)).toEqual(["ANT"]);
        });
    });

    describe("summary filter tiles", () => {
        // Fixture states: Zebra (ZNT) online, Maple (MNT) degraded, Acme (ANT) offline.
        it("renders one tile per connection state with its count", async () => {
            mockSwrvData.value = fixture;
            const wrapper = mountPage();
            await wrapper.vm.$nextTick();

            expect(wrapper.find("[data-test='filter-tile-count-online']").text()).toBe("1");
            expect(wrapper.find("[data-test='filter-tile-count-degraded']").text()).toBe("1");
            expect(wrapper.find("[data-test='filter-tile-count-offline']").text()).toBe("1");
        });

        it("filters the table to a state on click and resets on a second click", async () => {
            mockSwrvData.value = fixture;
            const wrapper = mountPage();
            const tile = wrapper.find("[data-test='filter-tile-offline']");

            await tile.trigger("click");
            expect(tile.attributes("aria-pressed")).toBe("true");
            expect(codesInOrder(wrapper)).toEqual(["ANT"]);

            await tile.trigger("click");
            expect(tile.attributes("aria-pressed")).toBe("false");
            expect(codesInOrder(wrapper)).toEqual(["ANT", "MNT", "ZNT"]);
        });

        it("switches the filter when a different tile is clicked", async () => {
            mockSwrvData.value = fixture;
            const wrapper = mountPage();

            await wrapper.find("[data-test='filter-tile-offline']").trigger("click");
            await wrapper.find("[data-test='filter-tile-degraded']").trigger("click");
            expect(codesInOrder(wrapper)).toEqual(["MNT"]);
        });

        it("explains an empty filtered table instead of claiming no trusts exist", async () => {
            // All fixture trusts online → the offline filter empties the table.
            mockSwrvData.value = [fixture[0]];
            const wrapper = mountPage();

            await wrapper.find("[data-test='filter-tile-offline']").trigger("click");
            expect(wrapper.findAll("[data-test='trust-row']")).toHaveLength(0);
            expect(wrapper.text()).toContain("No trusts match this filter.");
            expect(wrapper.text()).not.toContain("No trusts registered yet.");
        });
    });
});
