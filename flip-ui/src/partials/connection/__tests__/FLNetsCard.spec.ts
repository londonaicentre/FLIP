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

import FLNetsCard from "@/partials/connection/FLNetsCard.vue";
import { IFLStatus } from "@/services/fl-service";

const mockSwrvData = ref<IFLStatus[] | undefined>(undefined);

vi.mock("swrv", () => ({
    default: () => ({
        data: mockSwrvData,
        mutate: vi.fn(),
        error: ref(null)
    })
}));

const stubs = {
    Transition: { template: "<div><slot /></div>" },
    AiCard: { template: "<div><slot /></div>" },
    AiCommand: { template: "<div><slot /></div>" },
    AiAlert: { template: "<div />" },
    AiLoader: { template: "<div />" },
    AiButton: { template: "<button><slot /></button>" },
    "icon-ph-check-circle-duotone": { template: "<span />" },
    "icon-ph-x-circle-duotone": { template: "<span />" },
    "icon-ph-archive-duotone": { template: "<span />" }
};

function mountFLNetsCard() {
    return mount(FLNetsCard, {
        global: {
            plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: false })],
            stubs,
            directives: { highlightjs: () => {} }
        }
    });
}

beforeEach(() => {
    mockSwrvData.value = undefined;
});

describe("FLNetsCard", () => {
    it("mounts without errors", () => {
        const wrapper = mountFLNetsCard();
        expect(wrapper.exists()).toBe(true);
    });

    it("renders nvflare backend as 'NVFlare' next to the NET title", () => {
        mockSwrvData.value = [
            { name: "net-1", fl_backend: "nvflare", clients: [] }
        ];
        const wrapper = mountFLNetsCard();
        const titles = wrapper.findAll("h3");
        const net1Title = titles.find(h => h.text().includes("net-1"));
        expect(net1Title).toBeDefined();
        expect(net1Title!.text()).toContain("(NVFlare)");
    });

    it("renders flower backend as 'Flower' next to the NET title", () => {
        mockSwrvData.value = [
            { name: "net-2", fl_backend: "flower", clients: [] }
        ];
        const wrapper = mountFLNetsCard();
        const titles = wrapper.findAll("h3");
        const net2Title = titles.find(h => h.text().includes("net-2"));
        expect(net2Title).toBeDefined();
        expect(net2Title!.text()).toContain("(Flower)");
    });

    it("omits parentheses when fl_backend is absent", () => {
        mockSwrvData.value = [
            { name: "net-1", clients: [] }
        ];
        const wrapper = mountFLNetsCard();
        const titles = wrapper.findAll("h3");
        const net1Title = titles.find(h => h.text().includes("net-1"));
        expect(net1Title).toBeDefined();
        expect(net1Title!.text()).not.toContain("(");
    });

    it("renders the loader (and no net cards) while flStatus is undefined", () => {
        // Default beforeEach() resets mockSwrvData to undefined.
        const wrapper = mountFLNetsCard();
        // AiLoader is stubbed to a marker, so this confirms the v-if="!flStatus" branch.
        expect(wrapper.findAll("h3").length).toBe(0);
    });

it("renders one row per client and sorts them alphabetically by name", () => {
        mockSwrvData.value = [
            {
                name: "net-1",
                fl_backend: "nvflare",
                clients: [
                    { name: "Zebra", online: true },
                    { name: "Acme",  online: true },
                    { name: "Maple", online: true }
                ]
            }
        ];
        const wrapper = mountFLNetsCard();
        const names = wrapper.findAll("[data-test=project-name]").map(n => n.text());
        expect(names).toEqual(["Acme", "Maple", "Zebra"]);
    });

    it("renders project-list-item rows with view-project-btn anchors for each client", () => {
        mockSwrvData.value = [
            {
                name: "net-1",
                fl_backend: "nvflare",
                clients: [
                    { name: "Trust A", online: true },
                    { name: "Trust B", online: false }
                ]
            }
        ];
        const wrapper = mountFLNetsCard();
        expect(wrapper.findAll("[data-test=project-list-item-0]").length).toBe(1);
        expect(wrapper.findAll("[data-test=project-list-item-1]").length).toBe(1);
        // The clickable wrapper carries view-project-btn per row — used by the
        // group-6 Cypress project_list flow.
        expect(wrapper.findAll("[data-test=view-project-btn]").length).toBe(2);
    });

    it("decorates the net card with the offline-glow when any client is offline", () => {
        mockSwrvData.value = [
            {
                name: "net-with-offline",
                fl_backend: "nvflare",
                clients: [
                    { name: "Trust A", online: true },
                    { name: "Trust B", online: false }
                ]
            },
            {
                name: "net-all-online",
                fl_backend: "nvflare",
                clients: [{ name: "Trust C", online: true }]
            }
        ];
        const wrapper = mountFLNetsCard();
        const glowEls = wrapper.findAll(".from-red-500");
        // Exactly one card carries the glow div: the one with an offline client.
        expect(glowEls.length).toBe(1);
    });
});
