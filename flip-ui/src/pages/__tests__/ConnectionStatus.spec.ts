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

import { IFLStatus } from "@/services/fl-service";

import ConnectionStatus from "../ConnectionStatus.vue";

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

function mountConnectionStatus() {
    return mount(ConnectionStatus, {
        global: {
            plugins: [createTestingPinia({
                createSpy: vi.fn,
                stubActions: false
            })],
            stubs,
            directives: { highlightjs: () => {} }
        }
    });
}

beforeEach(() => {
    mockSwrvData.value = undefined;
});

describe("ConnectionStatus", () => {
    it("mounts without errors", () => {
        const wrapper = mountConnectionStatus();
        expect(wrapper.exists()).toBe(true);
    });

    it("renders nvflare backend as 'NVFlare' next to the NET title", () => {
        mockSwrvData.value = [
            {
                name: "net-1",
                fl_backend: "nvflare",
                clients: []
            }
        ];
        const wrapper = mountConnectionStatus();
        const titles = wrapper.findAll("h3");
        const net1Title = titles.find(h => h.text().includes("net-1"));
        expect(net1Title).toBeDefined();
        expect(net1Title!.text()).toContain("(NVFlare)");
    });

    it("renders flower backend as 'Flower' next to the NET title", () => {
        mockSwrvData.value = [
            {
                name: "net-2",
                fl_backend: "flower",
                clients: []
            }
        ];
        const wrapper = mountConnectionStatus();
        const titles = wrapper.findAll("h3");
        const net2Title = titles.find(h => h.text().includes("net-2"));
        expect(net2Title).toBeDefined();
        expect(net2Title!.text()).toContain("(Flower)");
    });

    it("omits parentheses when fl_backend is absent", () => {
        mockSwrvData.value = [
            {
                name: "net-1",
                clients: []
            }
        ];
        const wrapper = mountConnectionStatus();
        const titles = wrapper.findAll("h3");
        const net1Title = titles.find(h => h.text().includes("net-1"));
        expect(net1Title).toBeDefined();
        expect(net1Title!.text()).not.toContain("(");
    });
});
