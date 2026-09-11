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
import { Form as VeeForm } from "vee-validate";
import { expect, it, vi } from "vitest";
import { defineComponent } from "vue";

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

    // A field that starts ON is where the two sources of truth used to diverge: the knob and
    // label came from vee-validate, while aria-checked (and Space) came from Headless UI's own
    // internal state, which was never told the field started on. Mount inside a form with an
    // initial value so the switch is drawn on from the first render.
    const mountOn = () =>
        mount(
            defineComponent({
                components: {
                    AiSwitch,
                    VeeForm
                },
                template: `
                    <VeeForm :initial-values="{ flag: true }">
                        <AiSwitch name="flag" :value="true" :label="{ enabled: 'On', disabled: 'Off' }" />
                    </VeeForm>`
            }),
            {
                global: {
                    plugins: [createTestingPinia({
                        createSpy: vi.fn,
                        stubActions: false
                    })]
                }
            }
        );

    it("announces the state it draws: aria-checked follows the knob, not a private counter", async () => {
        const comp = mountOn();
        const control = comp.get("button[role=\"switch\"]");

        expect(comp.get("[data-test=switch-knob]").classes()).toContain("translate-x-6");
        expect(comp.text()).toContain("On");
        expect(control.attributes("aria-checked")).toBe("true");

        await control.trigger("click");

        expect(comp.get("[data-test=switch-knob]").classes()).toContain("translate-x-1");
        expect(comp.text()).toContain("Off");
        expect(control.attributes("aria-checked")).toBe("false");
    });

    it("toggles from the keyboard: Space changes the value, not just the announcement", async () => {
        const comp = mountOn();
        const control = comp.get("button[role=\"switch\"]");

        await control.trigger("keyup", { key: " " });

        expect(comp.get("[data-test=switch-knob]").classes()).toContain("translate-x-1");
        expect(comp.text()).toContain("Off");
        expect(control.attributes("aria-checked")).toBe("false");
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

    it("a disabled switch is natively disabled, not just aria-disabled", async () => {
        // aria-disabled alone leaves the button focusable and Headless UI's internal
        // handlers reachable (they never check the prop, and would flip aria-checked
        // for screen readers even with the knob and form value guarded). The native
        // attribute makes the browser deliver no events at all and drops the dead
        // control from the tab order — jsdom dispatches events regardless, so this
        // pins the attribute rather than the resulting behaviour.
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

        expect(comp.find("button[role=\"switch\"]").attributes("disabled")).toBeDefined();
    });

    it("an enabled switch carries no native disabled attribute", async () => {
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

        expect(comp.find("button[role=\"switch\"]").attributes("disabled")).toBeUndefined();
    });

});
