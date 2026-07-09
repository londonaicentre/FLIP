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
import { describe, expect, it, vi } from "vitest";

import TrainingOptions from "@/partials/models/TrainingOptions.vue";

// AiSwitch is the vee-validate field; we stub it to expose the `name` and `value`
// it is bound to, so we can assert the trust is selected by its UUID id (not name).
const aiSwitchStub = {
    props: ["name", "value", "dataTest", "label", "hideError", "disabled"],
    template: "<button :data-test=\"dataTest\" :data-name=\"name\" :data-value=\"value\" " +
        ":disabled=\"disabled\" />"
};

function mountTrainingOptions(
    approvedTrusts: { name: string; id: string; approved: boolean }[],
    disabled = false
) {
    return mount(TrainingOptions, {
        global: {
            plugins: [
                createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false,
                    initialState: { project: { project: { approvedTrusts } } }
                })
            ],
            stubs: { AiSwitch: aiSwitchStub }
        },
        props: {
            errors: {},
            disabled
        }
    });
}

describe("TrainingOptions trust selection", () => {
    const trusts = [
        {
            name: "Beta Trust",
            id: "id-beta",
            approved: true
        },
        {
            name: "Alpha Trust",
            id: "id-alpha",
            approved: true
        },
        {
            name: "Gamma Trust",
            id: "id-gamma",
            approved: false
        }
    ];

    it("binds each trust's UUID id (not its name) as the selectable value", () => {
        const wrapper = mountTrainingOptions(trusts);
        const switches = wrapper.findAll("[data-test^='trust-selection-']");

        // Two approved trusts, sorted by display name: Alpha then Beta.
        expect(switches).toHaveLength(2);
        expect(switches[0].attributes("data-value")).toBe("id-alpha");
        expect(switches[1].attributes("data-value")).toBe("id-beta");
        // The display name must never leak into the value.
        expect(switches[0].attributes("data-value")).not.toBe("Alpha Trust");
    });

    it("collects the selected trusts under the `trust_ids` form field", () => {
        const wrapper = mountTrainingOptions(trusts);
        const switches = wrapper.findAll("[data-test^='trust-selection-']");

        for (const sw of switches) {
            expect(sw.attributes("data-name")).toBe("trust_ids");
        }
    });

    it("shows the trust display name as the label and excludes un-approved trusts", () => {
        const wrapper = mountTrainingOptions(trusts);
        const text = wrapper.text();

        expect(text).toContain("Alpha Trust");
        expect(text).toContain("Beta Trust");
        // Gamma is not approved, so it must not be offered for training.
        expect(text).not.toContain("Gamma Trust");
    });

    it("surfaces the trust_ids validation error when present", () => {
        const wrapper = mount(TrainingOptions, {
            global: {
                plugins: [
                    createTestingPinia({
                        createSpy: vi.fn,
                        stubActions: false,
                        initialState: { project: { project: { approvedTrusts: trusts } } }
                    })
                ],
                stubs: { AiSwitch: aiSwitchStub }
            },
            props: { errors: { trust_ids: "You must select a minimum of one trust for training." } }
        });

        expect(wrapper.text()).toContain("You must select a minimum of one trust for training.");
    });
});

describe("TrainingOptions disabled", () => {
    const trust = [{
        name: "Alpha Trust",
        id: "id-alpha",
        approved: true
    }];

    it("locks every control once the run is under way, so the choices stay readable", () => {
        const comp = mountTrainingOptions(trust, true);

        const switches = comp.findAll("button");
        expect(switches.length).toBeGreaterThan(0);
        expect(switches.every((s) => s.attributes("disabled") !== undefined)).toBe(true);
    });

    it("leaves the controls live while the model is still being prepared", () => {
        const comp = mountTrainingOptions(trust);

        expect(comp.findAll("button").every((s) => s.attributes("disabled") === undefined)).toBe(true);
    });
});
