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

import { IAdminTrust } from "@/services/admin-trusts-service";

import ConnectionStatus from "../ConnectionStatus.vue";

const mockSwrvData = ref<IAdminTrust[] | undefined>(undefined);

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
        // Forward attrs (including data-test) so tests can target this stubbed button.
        template: "<button v-bind=\"$attrs\"><slot /></button>",
        inheritAttrs: false
    },
    AiLoader: { template: "<div data-test=\"ai-loader\" />" },
    AddTrustModal: { template: "<div />" },
    TrustKitModal: { template: "<div />" },
    // Stubbed: the shared swrv mock returns trust fixtures, which the partial
    // (which expects IFLStatus shape) would otherwise misinterpret.
    FLNetsCard: { template: "<div />" },
    "icon-ph-list-bullets-duotone": { template: "<span />" },
    "icon-ph-share-network-duotone": { template: "<span />" }
};

const now = Date.now();
const seconds = (n: number) => new Date(now - n * 1000).toISOString();

const fixture: IAdminTrust[] = [
    {
        id: "t1",
        name: "Zebra NHS Trust",
        code: "ZNT",
        region: "London",
        created_at: null,
        disabled_at: null,
        last_heartbeat: seconds(10),
        project_count: 1
    },
    {
        id: "t2",
        name: "Acme NHS Trust",
        code: "ANT",
        region: "South West",
        created_at: null,
        disabled_at: null,
        last_heartbeat: null,
        project_count: 7
    },
    {
        id: "t3",
        name: "Maple NHS Trust",
        code: "MNT",
        region: "North East",
        created_at: null,
        disabled_at: null,
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
                initialState: {
                    auth: { user: { permissions } }
                }
            })],
            stubs
        }
    });
}

const codesInOrder = (wrapper: ReturnType<typeof mountPage>): string[] =>
    wrapper.findAll("[data-test='trust-row']").map(r => {
        const heading = r.find("td:nth-child(2) span.font-semibold");

        return heading.text();
    });

beforeEach(() => {
    mockSwrvData.value = undefined;
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

    it("shows the Add Trust button for admins", async () => {
        mockSwrvData.value = fixture;
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();
        expect(wrapper.find("[data-test='add-trust-btn']").exists()).toBe(true);
    });

    it("renders one row per trust with each trust's code (or name when no code)", async () => {
        mockSwrvData.value = [
            { ...fixture[0], code: undefined as unknown as string }, // codeless trust falls back to name
            fixture[1]
        ];
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();
        const codes = codesInOrder(wrapper);
        // Sorted alphabetically by display label — "Acme NHS Trust" (ANT code) before "Zebra NHS Trust" (no code → name).
        expect(codes).toEqual(["ANT", "Zebra NHS Trust"]);
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
});
