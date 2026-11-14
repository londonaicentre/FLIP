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
import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import AiSwitch from "@/components/AiSwitch/AiSwitch.vue";
import EditProjectDrawer from "@/partials/projects/EditProjectDrawer.vue";
import ProjectUsers from "@/partials/projects/ProjectUsers.vue";
import router from "@/router";
import { deleteProject } from "@/services/project-service";
import { useErrorStore } from "@/store/error";
import { Snackbar } from "@/utils/snackbar";

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

vi.mock("@/utils/snackbar", () => ({
    Snackbar: {
        success: vi.fn(),
        error: vi.fn()
    }
}));

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
        dicomToNifti: true,
        hasImaging: true
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

describe("EditProjectDrawer read-only creation-time fields", () => {
    const baseProps = {
        show: true,
        name: "Acme",
        id: "proj-1",
        description: "desc",
        projectUnstaged: true,
        updating: false,
        users: [],
        ownerId: "owner-1",
        dicomToNifti: true,
        hasImaging: true
    };

    const mountDrawer = (overrides: Partial<typeof baseProps> = {}) =>
        mountComponent(EditProjectDrawer, {
            global: {
                renderStubDefaultSlot: true,
                stubs: { AiConfirmModal: confirmModalStub }
            },
            props: {
                ...baseProps,
                ...overrides
            }
        });

    it("renders the DICOM-to-NIfTI section with the immutability hint", () => {
        const html = mountDrawer().html();

        expect(html).toContain("Convert DICOMs to NIfTI");
        expect(html).toContain("cannot be changed");
    });

    it("renders the toggle as read-only: on screen, in position, but not interactive", () => {
        const toggle = mountDrawer().find("[data-test=dicom-to-nifti-toggle]");

        expect(toggle.exists()).toBe(true);
        expect(toggle.attributes("aria-disabled")).toBe("true");
    });

    it("shows 'Enabled' when the project's dicom_to_nifti is on", () => {
        expect(mountDrawer().text()).toContain("Enabled");
    });

    it("shows 'Disabled' when the project's dicom_to_nifti is off", () => {
        // The drawer now carries a second switch (imaging, on here), so the assertion is scoped
        // to the DICOM switch's own rendered label rather than the whole drawer text.
        const dicomSwitch = mountDrawer({ dicomToNifti: false })
            .findAllComponents(AiSwitch)
            .find(c => c.props("name") === "dicom_to_nifti");

        expect(dicomSwitch?.text()).toContain("Disabled");
        expect(dicomSwitch?.text()).not.toContain("Enabled");
    });

    it("renders the imaging section read-only with the immutability hint", () => {
        const wrapper = mountDrawer();

        expect(wrapper.html()).toContain("Includes imaging data");
        expect(wrapper.find("[data-test=has-imaging-toggle]").attributes("aria-disabled")).toBe("true");
    });

    it("hides the DICOM-to-NIfTI section when the project has no imaging", () => {
        const wrapper = mountDrawer({ hasImaging: false });

        expect(wrapper.find("[data-test=has-imaging-toggle]").exists()).toBe(true);
        expect(wrapper.find("[data-test=dicom-to-nifti-toggle]").exists()).toBe(false);
        expect(wrapper.html()).not.toContain("Convert DICOMs to NIfTI");
    });
});

describe("EditProjectDrawer editing and deleting", () => {
    const alice = {
        id: "u1",
        email: "alice@e.com",
        name: "Alice",
        organisation: "Org",
        isDisabled: false
    };
    const baseProps = {
        show: true,
        name: "Acme",
        id: "proj-1",
        description: "desc",
        projectUnstaged: true,
        updating: false,
        users: [alice],
        ownerId: "owner-1",
        dicomToNifti: true,
        hasImaging: true
    };
    // Exposes the continue action so the delete flow runs without the typed confirmation.
    const confirmStub = {
        props: ["dialog", "continueAction"],
        template: "<div v-if=\"dialog\" data-test=\"confirm-open\">"
            + "<button data-test=\"confirm-delete\" @click=\"continueAction\" /></div>"
    };

    const mountDrawer = (props = {}, permissions: string[] = []) => mount(EditProjectDrawer, {
        global: {
            renderStubDefaultSlot: true,
            directives: { tippy: () => {} },
            stubs: { AiConfirmModal: confirmStub },
            plugins: [createTestingPinia({
                createSpy: vi.fn,
                stubActions: false,
                initialState: {
                    auth: {
                        user: {
                            attributes: { sub: "owner-1" },
                            permissions
                        }
                    }
                }
            })]
        },
        props: {
            ...baseProps,
            ...props
        }
    });

    it("emits save with the edited details and the project's current users", async () => {
        const wrapper = mountDrawer();

        await wrapper.find("[data-test=project-name]").setValue("Renamed");
        await wrapper.find("form").trigger("submit");

        await vi.waitFor(() => expect(wrapper.emitted("save")).toEqual([[{
            name: "Renamed",
            description: "desc",
            users: ["u1"]
        }]]));
    });

    it("saves the user list as last changed in the users section", async () => {
        const wrapper = mountDrawer();

        wrapper.findComponent(ProjectUsers).vm.$emit("updatedUsers", [alice, {
            id: "u2",
            email: "bob@e.com",
            isDisabled: false
        }]);
        await wrapper.find("form").trigger("submit");

        await vi.waitFor(() => expect(wrapper.emitted("save")?.[0][0]).toMatchObject({ users: ["u1", "u2"] }));
    });

    it("emits close from the header close button", async () => {
        const wrapper = mountDrawer();

        await wrapper.find("button[type=button]").trigger("click");

        expect(wrapper.emitted("close")).toHaveLength(1);
    });

    it("locks the details once the project is staged", () => {
        const wrapper = mountDrawer({ projectUnstaged: false });

        expect(wrapper.find("[data-test=project-name]").attributes("disabled")).toBeDefined();
        expect(wrapper.find("[data-test=update-project-btn]").exists()).toBe(false);
    });

    it("offers Delete Project to a non-owner holding CanDeleteAnyProject", () => {
        const wrapper = mountDrawer({ ownerId: "someone-else" }, ["CanDeleteAnyProject"]);

        expect(wrapper.find("[data-test=delete-project-btn]").exists()).toBe(true);
    });

    it("deletes the project, returns to the project list and confirms", async () => {
        vi.mocked(deleteProject).mockResolvedValueOnce(undefined as never);
        const wrapper = mountDrawer();

        await wrapper.find("[data-test=delete-project-btn]").trigger("click");
        await wrapper.find("[data-test=confirm-delete]").trigger("click");
        await flushPromises();

        expect(deleteProject).toHaveBeenCalledWith("/projects/proj-1");
        expect(router.push).toHaveBeenCalledWith({ path: "/projects" });
        expect(Snackbar.success).toHaveBeenCalledWith(expect.objectContaining({ title: "Project deleted" }));
        expect(wrapper.find("[data-test=confirm-open]").exists()).toBe(false);
    });

    it("reports a failed delete, closes the drawer and stays on the page", async () => {
        vi.mocked(deleteProject).mockRejectedValueOnce(new Error("boom"));
        const wrapper = mountDrawer();

        await wrapper.find("[data-test=delete-project-btn]").trigger("click");
        await wrapper.find("[data-test=confirm-delete]").trigger("click");
        await flushPromises();

        expect(Snackbar.error).toHaveBeenCalledWith({
            title: "Unable to delete this project",
            text: "The project \"Acme\" has not been deleted."
        });
        expect(useErrorStore().hasError).toBe(true);
        expect(wrapper.emitted("close")).toHaveLength(1);
        expect(router.push).not.toHaveBeenCalled();
    });
});
