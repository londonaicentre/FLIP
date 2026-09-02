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
import { vi } from "vitest";

import NewProject from "../CreateProjectModal.vue";
import { CreateProjectModal } from "../selectors";

const mockCreateProject = vi.fn().mockResolvedValue({ id: "test-id" });

vi.mock("@/services/project-service", async (importOriginal) => {
    const actual = await importOriginal<typeof import("@/services/project-service")>();

    return {
        ...actual,
        createProject: (...args: unknown[]) => mockCreateProject(...args)
    };
});

vi.mock("@/router", () => ({ routeChange: { viewProject: vi.fn() } }));

const stubs = {
    TransitionRoot: { template: "<div><slot /></div>" },
    Dialog: { template: "<div><slot /></div>" },
    DialogPanel: { template: "<div><slot /></div>" },
    DialogTitle: { template: "<div><slot /></div>" },
    TransitionChild: { template: "<div><slot /></div>" },
    // The scoped slot mirrors vee-validate's Form, which exposes the live `values` to its default slot.
    Form: { template: "<form @submit.prevent=\"$emit('submit', $attrs['initial-values'])\"><slot :values=\"$attrs['initial-values']\" /></form>" },
    "icon-mdi-close": { template: "<span>×</span>" },
    "ProjectUsers": { template: "<div>Project Users Component</div>" }
};

describe("Create Project Modal", () => {
    let component: ReturnType<typeof mount>;

    beforeEach(() => {
        mockCreateProject.mockClear();

        component = mount(NewProject, {
            props: { open: true },
            global: {
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })],
                stubs
            }
        });
    });

    it("Renders the component", () => {
        expect(component.exists()).toBe(true);
    });

    it("Project name input exists", async () => {
        const nameInput = component.find(CreateProjectModal.projectNameInput);
        expect(nameInput.exists()).toBe(true);
    });

    it("Project description input exists", async () => {
        const descriptionInput = component.find(
            CreateProjectModal.projectDescription
        );
        expect(descriptionInput.exists()).toBe(true);
    });

    it("DICOM to NIfTI toggle exists", () => {
        const toggle = component.find(CreateProjectModal.dicomToNiftiToggle);
        expect(toggle.exists()).toBe(true);
    });

    it("dicom_to_nifti string 'true' is coerced to boolean true on submit", async () => {
        const form = component.find("form");
        await form.trigger("submit");
        await vi.waitFor(() => expect(mockCreateProject).toHaveBeenCalled());

        const submittedValues = mockCreateProject.mock.calls[0][1];
        expect(submittedValues.dicom_to_nifti).toBe(true);
        expect(typeof submittedValues.dicom_to_nifti).toBe("boolean");
    });

    it("dicom_to_nifti falsy value is coerced to boolean false on submit", async () => {
        component = mount(NewProject, {
            props: { open: true },
            global: {
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })],
                stubs: {
                    ...stubs,
                    Form: {
                        template: "<form @submit.prevent=\"$emit('submit', $attrs['initial-values'])\"><slot :values=\"$attrs['initial-values']\" /></form>",
                        mounted() {
                            // Override initial-values to simulate unchecked toggle
                            // eslint-disable-next-line @typescript-eslint/no-explicit-any
                            (this as any).$attrs["initial-values"].dicom_to_nifti = "";
                        }
                    }
                }
            }
        });

        const form = component.find("form");
        await form.trigger("submit");
        await vi.waitFor(() => expect(mockCreateProject).toHaveBeenCalled());

        const submittedValues = mockCreateProject.mock.calls[0][1];
        expect(submittedValues.dicom_to_nifti).toBe(false);
        expect(typeof submittedValues.dicom_to_nifti).toBe("boolean");
    });

    it("Includes imaging data toggle exists and defaults on, so the DICOM to NIfTI toggle is shown", () => {
        expect(component.find(CreateProjectModal.hasImagingToggle).exists()).toBe(true);
        expect(component.find(CreateProjectModal.dicomToNiftiToggle).exists()).toBe(true);
    });

    it("has_imaging string 'true' is coerced to boolean true on submit", async () => {
        await component.find("form").trigger("submit");
        await vi.waitFor(() => expect(mockCreateProject).toHaveBeenCalled());

        const submittedValues = mockCreateProject.mock.calls[0][1];
        expect(submittedValues.has_imaging).toBe(true);
        expect(submittedValues.dicom_to_nifti).toBe(true);
    });

    it("turning imaging off hides the DICOM to NIfTI toggle and submits has_imaging=false with dicom_to_nifti at its default", async () => {
        component = mount(NewProject, {
            props: { open: true },
            global: {
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })],
                stubs: {
                    ...stubs,
                    // The real vee-validate Form drops an unmounted field from `values`; mirror that: no dicom_to_nifti key.
                    Form: { template: "<form @submit.prevent=\"$emit('submit', { has_imaging: '' })\"><slot :values=\"{ has_imaging: '' }\" /></form>" }
                }
            }
        });

        expect(component.find(CreateProjectModal.hasImagingToggle).exists()).toBe(true);
        expect(component.find(CreateProjectModal.dicomToNiftiToggle).exists()).toBe(false);

        await component.find("form").trigger("submit");
        await vi.waitFor(() => expect(mockCreateProject).toHaveBeenCalled());

        const submittedValues = mockCreateProject.mock.calls[0][1];
        expect(submittedValues.has_imaging).toBe(false);
        expect(submittedValues.dicom_to_nifti).toBe(true);
    });
});
