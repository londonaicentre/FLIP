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
    AiButton: { template: "<button><slot /></button>" },
    AiLoader: { template: "<div />" },
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

function mountPage() {
    return mount(ConnectionStatus, {
        global: {
            plugins: [createTestingPinia({
                createSpy: vi.fn,
                stubActions: false
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
});
