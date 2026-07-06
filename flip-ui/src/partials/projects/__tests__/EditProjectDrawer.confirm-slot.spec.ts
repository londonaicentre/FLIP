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
import { mountComponent } from "@test/helper";
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import EditProjectDrawer from "@/partials/projects/EditProjectDrawer.vue";

vi.mock("vue-router", async (importOriginal) => {
    const actual = await importOriginal<typeof import("vue-router")>();

    return {
        ...actual,
        useRoute: () => ({
            params: {},
            query: {}
        })
    };
});

vi.mock("@/router", () => ({
    default: {
        push: vi.fn(),
        replace: vi.fn()
    }
}));

vi.mock("@/services/project-service", () => ({ deleteProject: vi.fn() }));

const confirmModalStub = { template: "<div data-test=\"confirm-modal-stub\"><slot name=\"confirmation\" /></div>" };

describe("EditProjectDrawer delete-confirmation slot", () => {
    const baseProps = {
        show: true,
        name: "Acme",
        id: "proj-1",
        description: "desc",
        projectUnstaged: true,
        updating: false,
        users: [],
        ownerId: "owner-1",
        dicomToNifti: true
    };

    it("renders the delete-project warning markup in the confirmation slot", () => {
        const wrapper = mountComponent(EditProjectDrawer, {
            global: {
                renderStubDefaultSlot: true,
                stubs: { AiConfirmModal: confirmModalStub }
            },
            props: baseProps
        });

        const html = wrapper.html();

        expect(html).toContain("Any active training jobs");
        expect(html).toContain("Your username will be recorded against this action");
        expect(html).toContain("To delete this project, enter");
    });

    it("escapes the project name in the confirmation slot", () => {
        const payload = "<img src=x onerror=alert(1)>";
        const wrapper = mountComponent(EditProjectDrawer, {
            global: {
                renderStubDefaultSlot: true,
                stubs: { AiConfirmModal: confirmModalStub }
            },
            props: {
                ...baseProps,
                name: payload
            }
        });

        const slot = wrapper.find("[data-test=confirm-modal-stub]");

        expect(slot.exists()).toBe(true);
        expect(slot.find("img").exists()).toBe(false);
        expect(slot.html()).toContain("&lt;img");
    });

    it("renders the Advanced Options / delete section when the user owns the project", () => {
        const wrapper = mount(EditProjectDrawer, {
            global: {
                renderStubDefaultSlot: true,
                mocks: { getRandomId: () => "random-id" },
                stubs: { AiConfirmModal: confirmModalStub },
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false,
                    // sub matches baseProps.ownerId -> canDeleteProject() is true, so the
                    // (dark-mode) delete section renders.
                    initialState: {
                        auth: {
                            user: {
                                attributes: { sub: "owner-1" },
                                permissions: []
                            }
                        }
                    }
                })]
            },
            props: baseProps
        });

        expect(wrapper.text()).toContain("Advanced Options");
    });

    it("hides the delete section when the user cannot delete the project", () => {
        const wrapper = mountComponent(EditProjectDrawer, {
            global: {
                renderStubDefaultSlot: true,
                stubs: { AiConfirmModal: confirmModalStub }
            },
            props: baseProps
        });

        expect(wrapper.text()).not.toContain("Advanced Options");
    });
});

describe("EditProjectDrawer DICOM-to-NIfTI read-only field", () => {
    const baseProps = {
        show: true,
        name: "Acme",
        id: "proj-1",
        description: "desc",
        projectUnstaged: true,
        updating: false,
        users: [],
        ownerId: "owner-1",
        dicomToNifti: true
    };

    const mountDrawer = (dicomToNifti: boolean) =>
        mountComponent(EditProjectDrawer, {
            global: {
                renderStubDefaultSlot: true,
                stubs: { AiConfirmModal: confirmModalStub }
            },
            props: {
                ...baseProps,
                dicomToNifti
            }
        });

    it("renders the DICOM-to-NIfTI section with the immutability hint", () => {
        const html = mountDrawer(true).html();

        expect(html).toContain("Convert DICOMs to NIfTI");
        expect(html).toContain("cannot be changed");
    });

    it("renders the toggle as read-only (the interactive switch is hidden while disabled)", () => {
        const wrapper = mountDrawer(true);

        expect(wrapper.find("[data-test=dicom-to-nifti-toggle]").exists()).toBe(false);
    });

    it("shows 'Enabled' when the project's dicom_to_nifti is on", () => {
        expect(mountDrawer(true).text()).toContain("Enabled");
    });

    it("shows 'Disabled' when the project's dicom_to_nifti is off", () => {
        const text = mountDrawer(false).text();

        expect(text).toContain("Disabled");
        // The capitalised "Enabled" label only renders when the toggle is on;
        // the descriptive copy uses lowercase "enabled", so this stays exact.
        expect(text).not.toContain("Enabled");
    });
});
