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

vi.mock("@/router", () => ({
    routeChange: { viewProject: vi.fn() }
}));

const stubs = {
    TransitionRoot: { template: '<div><slot /></div>' },
    Dialog: { template: '<div><slot /></div>' },
    DialogPanel: { template: '<div><slot /></div>' },
    DialogTitle: { template: '<div><slot /></div>' },
    TransitionChild: { template: '<div><slot /></div>' },
    Form: { template: '<form @submit.prevent="$emit(\'submit\', $attrs[\'initial-values\'])"><slot /></form>' },
    'icon-mdi-close': { template: '<span>×</span>' },
    'ProjectUsers': { template: '<div>Project Users Component</div>' }
};

describe("Create Project Modal", () => {
    let component: any;

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
                        template: '<form @submit.prevent="$emit(\'submit\', $attrs[\'initial-values\'])"><slot /></form>',
                        mounted() {
                            // Override initial-values to simulate unchecked toggle
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
});
