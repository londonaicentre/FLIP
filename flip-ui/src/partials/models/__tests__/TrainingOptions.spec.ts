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

import { FileUploadStatus, IRequiredFile } from "@/interfaces/model/types";
import TrainingOptions from "@/partials/models/TrainingOptions.vue";
import type { IProjectTrust } from "@/services/project-service";

// AiSwitch is the vee-validate field; we stub it to expose the `name` and `value`
// it is bound to, so we can assert the trust is selected by its UUID id (not name).
const aiSwitchStub = {
    props: ["name", "value", "dataTest", "label", "ariaLabel", "hideError", "disabled"],
    template: "<button :data-test=\"dataTest\" :data-name=\"name\" :data-value=\"value\" " +
        ":aria-label=\"ariaLabel\" :data-label=\"label && JSON.stringify(label)\" :disabled=\"disabled\" />"
};

function mountTrainingOptions(
    approvedTrusts: IProjectTrust[],
    disabled = false,
    hasImaging: boolean | undefined = undefined,
    flBackendLabel: string | undefined = undefined,
    files: { requiredFiles?: IRequiredFile[] } = {}
) {
    return mount(TrainingOptions, {
        global: {
            plugins: [
                createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false,
                    initialState: {
                        project: {
                            project: {
                                approvedTrusts,
                                has_imaging: hasImaging
                            }
                        }
                    }
                })
            ],
            stubs: { AiSwitch: aiSwitchStub }
        },
        props: {
            errors: {},
            disabled,
            flBackendLabel,
            ...files
        }
    });
}

describe("TrainingOptions trust selection", () => {
    const trusts: IProjectTrust[] = [
        {
            name: "Beta Trust",
            id: "id-beta",
            code: "BETA",
            status: "APPROVED"
        },
        {
            name: "Alpha Trust",
            id: "id-alpha",
            code: "ALPHA",
            status: "APPROVED"
        },
        {
            name: "Gamma Trust",
            id: "id-gamma",
            code: "GAMMA",
            status: "DECLINED"
        },
        {
            // Left out of an approval made before FLIP#1318, so migrated to PENDING, not DECLINED.
            name: "Delta Trust",
            id: "id-delta",
            code: "DELTA",
            status: "PENDING"
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

    it("names each trust switch after its trust, with no Included/Excluded text", () => {
        const wrapper = mountTrainingOptions(trusts);
        const switches = wrapper.findAll("[data-test^='trust-selection-']");

        expect(switches[0].attributes("aria-label")).toBe("Alpha Trust (ALPHA)");
        expect(switches[0].attributes("data-label")).toBeUndefined();
    });

    it("collects the selected trusts under the `trust_ids` form field", () => {
        const wrapper = mountTrainingOptions(trusts);
        const switches = wrapper.findAll("[data-test^='trust-selection-']");

        for (const sw of switches) {
            expect(sw.attributes("data-name")).toBe("trust_ids");
        }
    });

    it("labels each trust with its code, and excludes trusts that are not approved", () => {
        const wrapper = mountTrainingOptions(trusts);
        const text = wrapper.text();

        // Names are admin-chosen and non-unique, so the code disambiguates them.
        expect(text).toContain("Alpha Trust (ALPHA)");
        expect(text).toContain("Beta Trust (BETA)");
        // Gamma declined and Delta was never decided, so neither may be offered for training.
        expect(text).not.toContain("Gamma Trust");
        expect(text).not.toContain("Delta Trust");
    });

    it("falls back to the bare name when a trust carries no code", () => {
        const wrapper = mountTrainingOptions([
            {
                name: "Alpha Trust",
                id: "id-alpha",
                code: null,
                status: "APPROVED"
            },
            {
                name: "Beta Trust",
                id: "id-beta",
                status: "APPROVED"
            }
        ]);
        const text = wrapper.text();

        expect(text).toContain("Alpha Trust");
        expect(text).toContain("Beta Trust");
        // An absent code must never render as empty parentheses.
        expect(text).not.toContain("(");
    });

    it("orders the trusts by name, not by the code appended to it", () => {
        const wrapper = mountTrainingOptions([
            {
                name: "Beta Trust",
                id: "id-beta",
                code: "AAA",
                status: "APPROVED"
            },
            {
                name: "Alpha Trust",
                id: "id-alpha",
                code: "ZZZ",
                status: "APPROVED"
            }
        ]);
        const labels = wrapper.findAll("dt").map(dt => dt.text());

        expect(labels).toEqual(["Alpha Trust (ZZZ)", "Beta Trust (AAA)"]);
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
    const trust: IProjectTrust[] = [{
        name: "Alpha Trust",
        id: "id-alpha",
        status: "APPROVED"
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

describe("TrainingOptions enrichment copy", () => {
    it("asks to confirm dataset enrichment for an imaging project", () => {
        const wrapper = mountTrainingOptions([], false, true);
        expect(wrapper.text()).toContain("Confirm your dataset has been enriched as required before training");
    });

    it("keeps the imaging copy when the hub omits has_imaging (a hub predating the flag)", () => {
        const wrapper = mountTrainingOptions([], false);
        expect(wrapper.text()).toContain("Confirm your dataset has been enriched as required before training");
    });

    it("explains there is no imaging to enrich for a tabular-only project", () => {
        const wrapper = mountTrainingOptions([], false, false);
        expect(wrapper.text()).toContain("This project has no imaging to enrich");
        expect(wrapper.find("[data-test=data-enrichment-btn]").exists()).toBe(true); // the gate itself stays
    });
});


describe("TrainingOptions GPU request (FLIP#70)", () => {
    it("offers an override switch that is off by default, so the job's own request applies", () => {
        const wrapper = mountTrainingOptions([]);

        const override = wrapper.get("[data-test=gpu-override-switch]");
        expect(override.attributes("data-name")).toBe("gpu_override");
        expect(wrapper.text()).toContain("meta.json");
        expect(wrapper.find("[data-test=gpu-count-input]").exists()).toBe(false);
    });

    it("locks the override with the rest of a dispatched run's options", () => {
        const wrapper = mountTrainingOptions([], true);

        expect(wrapper.get("[data-test=gpu-override-switch]").attributes("disabled")).toBeDefined();
    });

    it("says on a Flower net that the request is recorded, not enforced", () => {
        const wrapper = mountTrainingOptions([], false, undefined, "Flower");

        expect(wrapper.text()).toContain("Flower does not schedule jobs by GPU");
    });

    it("says nothing about Flower on an NVFlare net", () => {
        const wrapper = mountTrainingOptions([], false, undefined, "NVFlare");

        expect(wrapper.text()).not.toContain("Flower does not schedule jobs by GPU");
    });
});

describe("TrainingOptions required files row", () => {
    const { COMPLETED, SCANNING, UPLOADING, ERROR, INFECTED } = FileUploadStatus;
    const mountWithFiles = (requiredFiles: IRequiredFile[]) =>
        mountTrainingOptions([], false, undefined, undefined, { requiredFiles });
    const summary = (w: ReturnType<typeof mountWithFiles>) => w.find("[data-test=required-files-summary]");
    const pill = (w: ReturnType<typeof mountWithFiles>) => w.find("[data-test=required-files-status]");

    it("lists the missing files and counts the ready ones", () => {
        const wrapper = mountWithFiles([
            { name: "client_app.py" },
            {
                name: "config.json",
                status: COMPLETED
            },
            { name: "models.py" }
        ]);

        expect(summary(wrapper).text()).toBe("Missing client_app.py, models.py");
        expect(pill(wrapper).text()).toBe("1 of 3");
        expect(pill(wrapper).classes()).toContain("bg-amber-100");
    });

    it("reads Checking while the only thing left is uploads and checks finishing", () => {
        const wrapper = mountWithFiles([
            {
                name: "client_app.py",
                status: UPLOADING
            },
            {
                name: "config.json",
                status: SCANNING
            },
            {
                name: "models.py",
                status: COMPLETED
            }
        ]);

        expect(summary(wrapper).text()).toBe("Uploading client_app.py · Checking config.json");
        expect(pill(wrapper).text()).toBe("Checking");
        expect(pill(wrapper).classes()).toContain("bg-amber-100");
    });

    it("names failed and unsafe files first and keeps counting", () => {
        const wrapper = mountWithFiles([
            {
                name: "client_app.py",
                status: SCANNING
            },
            {
                name: "config.json",
                status: ERROR
            },
            {
                name: "model.pt",
                status: INFECTED
            },
            {
                name: "models.py",
                status: COMPLETED
            }
        ]);

        expect(summary(wrapper).text())
            .toBe("Unsafe content model.pt · Upload failed config.json · Checking client_app.py");
        expect(pill(wrapper).text()).toBe("1 of 4");
    });

    it("reads complete, in green, once every required file has passed its check", () => {
        const wrapper = mountWithFiles([{
            name: "config.json",
            status: COMPLETED
        }]);

        expect(summary(wrapper).text()).toBe("All required files uploaded");
        expect(pill(wrapper).text()).toBe("Complete");
        expect(pill(wrapper).classes()).toContain("bg-green-100");
    });

    it("is the first row of the checklist", () => {
        const wrapper = mountWithFiles([{ name: "config.json" }]);
        const row = wrapper.find("[data-test=required-files-row]");

        expect(row.element.parentElement?.firstElementChild).toBe(row.element);
    });

    it("is hidden when the required files are unknown", () => {
        const wrapper = mountWithFiles([]);

        expect(wrapper.find("[data-test=required-files-row]").exists()).toBe(false);
    });

    it("escapes file names so user-controlled values cannot inject HTML", () => {
        const payload = "<img src=x onerror=alert(1)>";
        const wrapper = mountWithFiles([{ name: payload }]);

        expect(summary(wrapper).find("img").exists()).toBe(false);
        expect(summary(wrapper).html()).toContain("&lt;img");
    });
});
