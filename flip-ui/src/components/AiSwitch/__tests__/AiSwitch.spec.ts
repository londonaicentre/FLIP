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
import { expect, it, vi } from "vitest";

import * as helpers from "@/utils/helpers";

import AiSwitch from "../AiSwitch.vue";

describe("AiSwitch", () => {
    it("Renders Component", () => {
        vi.spyOn(helpers, "getRandomId").mockImplementationOnce(() => "random-id");

        const comp = mount(AiSwitch, {
            props: {
                name: "something",
                value: "test"
            },
            global: {
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })]
            }
        });

        // Test functionality instead of snapshots
        expect(comp.exists()).toBe(true);
        expect(comp.find("button[role=\"switch\"]").exists()).toBe(true);
    });

    it("toggles on click, sliding the knob across — no tick, the position says it", async () => {
        const comp = mount(AiSwitch, {
            props: {
                name: "flag",
                value: true
            },
            global: {
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })]
            }
        });

        expect(comp.get("[data-test=switch-knob]").classes()).toContain("translate-x-1");
        expect(comp.find("svg").exists()).toBe(false);

        await comp.find("button[role=\"switch\"]").trigger("click");

        expect(comp.get("[data-test=switch-knob]").classes()).toContain("translate-x-6");
        // On, and still no tick: the knob has moved, which is the whole signal.
        expect(comp.find("svg").exists()).toBe(false);
    });

    it("drops its label on a narrow window — the knob's position already says it", () => {
        const comp = mount(AiSwitch, {
            props: {
                name: "flag",
                value: "true",
                label: {
                    enabled: "Trust Included",
                    disabled: "Trust Excluded"
                }
            },
            global: {
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })]
            }
        });

        const switchLabel = comp.get("label");
        expect(switchLabel.classes()).toContain("hidden");
        expect(switchLabel.classes()).toContain("sm:inline");
    });

    it("stays on screen when disabled, greyed out, so its position still reads", async () => {
        const comp = mount(AiSwitch, {
            props: {
                name: "flag",
                value: true,
                disabled: true
            },
            global: {
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })]
            }
        });

        const control = comp.find("button[role=\"switch\"]");
        expect(control.exists()).toBe(true);
        expect(control.attributes("aria-disabled")).toBe("true");
        expect(control.classes()).toContain("opacity-60");
        expect(control.classes()).toContain("cursor-not-allowed");
    });

    it("a disabled switch cannot be toggled", async () => {
        const comp = mount(AiSwitch, {
            props: {
                name: "flag",
                value: true,
                disabled: true
            },
            global: {
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })]
            }
        });

        // Off, and it must stay off: the knob does not move.
        await comp.find("button[role=\"switch\"]").trigger("click");

        expect(comp.get("[data-test=switch-knob]").classes()).toContain("translate-x-1");
    });

});
