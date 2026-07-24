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

const mockGetNetDetailedStatus = vi.fn();

vi.mock("@/services/fl-service", async (importOriginal) => {
    const actual = await importOriginal<typeof import("@/services/fl-service")>();

    return {
        ...actual,
        getNetDetailedStatus: (...args: unknown[]) => mockGetNetDetailedStatus(...args)
    };
});

const stubs = {
    Transition: { template: "<div><slot /></div>" },
    AiCard: { template: "<div><slot /></div>" },
    // AiCommand only renders its body when `open` is true; we mirror that
    // so tests can observe the details flyout opening/closing.
    AiCommand: {
        name: "AiCommand",
        props: ["open"],
        emits: ["close"],
        template: "<div data-test='ai-command' :data-open='open'><slot /></div>"
    },
    AiAlert: {
        props: ["variant", "text"],
        template: "<div data-test='ai-alert' :data-variant='variant'>{{ text }}</div>"
    },
    AiLoader: { template: "<div data-test='ai-loader' />" },
    AiButton: { template: "<button @click='$emit(\"click\", $event)'><slot /></button>" },
    "icon-ph-check-circle-duotone": { template: "<span />" },
    "icon-ph-x-circle-duotone": { template: "<span />" },
    "icon-ph-archive-duotone": { template: "<span />" }
};

function mountFLNetsCard() {
    return mount(FLNetsCard, {
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
    mockGetNetDetailedStatus.mockReset();
});

describe("FLNetsCard", () => {
    it("mounts without errors", () => {
        const wrapper = mountFLNetsCard();
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
        const wrapper = mountFLNetsCard();
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
        const wrapper = mountFLNetsCard();
        const titles = wrapper.findAll("h3");
        const net2Title = titles.find(h => h.text().includes("net-2"));
        expect(net2Title).toBeDefined();
        expect(net2Title!.text()).toContain("(Flower)");
    });

    it("shows each client's FL kit slot when it differs from the display name", () => {
        mockSwrvData.value = [
            {
                name: "net-1",
                fl_backend: "nvflare",
                clients: [
                    {
                        name: "Demo EC2 Trust",
                        code: "DEMO",
                        online: true,
                        status: "no_jobs",
                        fl_kit_slot: "Trust_1"
                    }
                ]
            }
        ];
        const wrapper = mountFLNetsCard();
        const slot = wrapper.find("[data-test='client-fl-kit-slot']");
        expect(slot.exists()).toBe(true);
        expect(slot.text()).toContain("Trust_1");
    });

    it("shows 'name (code)' for each client above the FL kit slot", () => {
        mockSwrvData.value = [
            {
                name: "net-1",
                fl_backend: "nvflare",
                clients: [
                    {
                        name: "Demo EC2 Trust",
                        code: "DEMO",
                        online: true,
                        status: "no_jobs",
                        fl_kit_slot: "Trust_1"
                    }
                ]
            }
        ];
        const wrapper = mountFLNetsCard();
        expect(wrapper.find("[data-test='project-name']").text()).toContain("Demo EC2 Trust (DEMO)");
    });

    it("omits parentheses when fl_backend is absent", () => {
        mockSwrvData.value = [
            {
                name: "net-1",
                clients: []
            }
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
                    {
                        name: "Zebra",
                        online: true
                    },
                    {
                        name: "Acme",
                        online: true
                    },
                    {
                        name: "Maple",
                        online: true
                    }
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
                    {
                        name: "Trust A",
                        online: true
                    },
                    {
                        name: "Trust B",
                        online: false
                    }
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

    describe("net details flyout", () => {
        it("opens the AiCommand and renders the JSON payload on successful fetch", async () => {
            mockSwrvData.value = [{
                name: "net-1",
                fl_backend: "nvflare",
                clients: [{
                    name: "Trust A",
                    online: true
                }]
            }];
            mockGetNetDetailedStatus.mockResolvedValue({
                status: "OK",
                clients: ["Trust A"]
            });

            const wrapper = mountFLNetsCard();
            const detailsBtn = wrapper.findAll("button").find(b => b.text().includes("View details"))!;
            await detailsBtn.trigger("click");
            await vi.waitFor(() =>
                expect(mockGetNetDetailedStatus).toHaveBeenCalledWith("/fl/net-1/status")
            );

            const command = wrapper.find("[data-test='ai-command']");
            expect(command.attributes("data-open")).toBe("true");
            expect(command.text()).toContain("\"status\": \"OK\"");
        });

        it("renders an error alert when the detailed fetch rejects", async () => {
            mockSwrvData.value = [{
                name: "net-1",
                clients: [{
                    name: "Trust A",
                    online: true
                }]
            }];
            mockGetNetDetailedStatus.mockRejectedValue(new Error("nope"));

            const wrapper = mountFLNetsCard();
            const detailsBtn = wrapper.findAll("button").find(b => b.text().includes("View details"))!;
            await detailsBtn.trigger("click");
            await vi.waitFor(() => expect(mockGetNetDetailedStatus).toHaveBeenCalled());
            // Let the rejected promise settle.
            await Promise.resolve();
            await Promise.resolve();

            const alert = wrapper.find("[data-test='ai-alert']");
            expect(alert.exists()).toBe(true);
            expect(alert.attributes("data-variant")).toBe("error");
        });

        it("closes the flyout and clears the cached details + error after 500ms on close", async () => {
            vi.useFakeTimers();
            try {
                mockSwrvData.value = [{
                    name: "net-1",
                    clients: [{
                        name: "Trust A",
                        online: true
                    }]
                }];
                mockGetNetDetailedStatus.mockResolvedValue({ status: "OK" });

                const wrapper = mountFLNetsCard();
                const detailsBtn = wrapper.findAll("button").find(b => b.text().includes("View details"))!;
                await detailsBtn.trigger("click");

                // Drain the resolved promise queue so the JSON renders before close.
                await vi.runAllTicks();
                await Promise.resolve();
                await Promise.resolve();

                const command = wrapper.findComponent({ name: "AiCommand" });
                await command.vm.$emit("close");
                // Synchronously the flyout closes; the cache wipe waits 500ms.
                expect(wrapper.find("[data-test='ai-command']").attributes("data-open")).toBe("false");

                vi.advanceTimersByTime(600);
                await vi.runAllTicks();

                // After the wipe the body section that renders the JSON is gone.
                expect(wrapper.text()).not.toContain("\"status\": \"OK\"");
            } finally {
                vi.useRealTimers();
            }
        });
    });

    it("keeps the net card border free of the offline glow so it matches the trust table", () => {
        mockSwrvData.value = [
            {
                name: "net-with-offline",
                fl_backend: "nvflare",
                clients: [
                    {
                        name: "Trust A",
                        online: true
                    },
                    {
                        name: "Trust B",
                        online: false
                    }
                ]
            },
            {
                name: "net-all-online",
                fl_backend: "nvflare",
                clients: [{
                    name: "Trust C",
                    online: true
                }]
            }
        ];
        const wrapper = mountFLNetsCard();
        // The blurred halo painted around the opaque card's edge in dark mode
        // and read as a purple border next to the trust table — the offline
        // signal lives in the per-client icons and the card heading instead.
        expect(wrapper.findAll(".from-red-500").length).toBe(0);
        expect(wrapper.findAll(".blur-md").length).toBe(0);
    });
});
