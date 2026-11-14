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

import EditModelDrawer from "@/partials/models/EditModelDrawer.vue";
import router from "@/router";
import { deleteModel } from "@/services/model-service";
import { useErrorStore } from "@/store/error";
import { Snackbar } from "@/utils/snackbar";

vi.mock("vue-router", async (importOriginal) => {
    const actual = await importOriginal<typeof import("vue-router")>();

    return {
        ...actual,
        useRoute: () => ({
            params: { projectId: "proj-1" },
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

vi.mock("@/services/model-service", () => ({ deleteModel: vi.fn() }));

vi.mock("@/utils/snackbar", () => ({
    Snackbar: {
        success: vi.fn(),
        error: vi.fn()
    }
}));

const confirmModalStub = { template: "<div data-test=\"confirm-modal-stub\"><slot name=\"confirmation\" /></div>" };

describe("EditModelDrawer delete-confirmation slot", () => {
    const baseProps = {
        id: "model-1",
        show: true,
        name: "Acme",
        description: "desc",
        modelPending: true,
        updating: false,
        ownerId: "owner-1"
    };

    it("renders the delete-model warning markup in the confirmation slot", () => {
        const wrapper = mountComponent(EditModelDrawer, {
            global: {
                renderStubDefaultSlot: true,
                stubs: { AiConfirmModal: confirmModalStub }
            },
            props: baseProps
        });

        const html = wrapper.html();

        expect(html).toContain("Training for this model will be stopped");
        expect(html).toContain("Your username will be recorded against this action");
        expect(html).toContain("To delete this model, enter");
    });

    it("escapes the model name in the confirmation slot", () => {
        const payload = "<img src=x onerror=alert(1)>";
        const wrapper = mountComponent(EditModelDrawer, {
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
});

describe("EditModelDrawer editing and deleting", () => {
    const baseProps = {
        id: "model-1",
        show: true,
        name: "Acme",
        description: "desc",
        modelPending: true,
        updating: false,
        ownerId: "owner-1"
    };
    // Exposes the continue action so the delete flow runs without the typed confirmation.
    const confirmStub = {
        props: ["dialog", "continueAction"],
        template: "<div v-if=\"dialog\" data-test=\"confirm-open\">"
            + "<button data-test=\"confirm-delete\" @click=\"continueAction\" /></div>"
    };

    const mountDrawer = (props = {}, user: object = {
        userId: "owner-1",
        permissions: ["CanCreateProjects"]
    }) => mount(EditModelDrawer, {
        global: {
            renderStubDefaultSlot: true,
            stubs: { AiConfirmModal: confirmStub },
            plugins: [createTestingPinia({
                createSpy: vi.fn,
                stubActions: false,
                initialState: { auth: { user } }
            })]
        },
        props: {
            ...baseProps,
            ...props
        }
    });

    it("emits save with the edited name and description", async () => {
        const wrapper = mountDrawer();

        await wrapper.find("[data-test=model-name]").setValue("Renamed");
        await wrapper.find("form").trigger("submit");

        await vi.waitFor(() => expect(wrapper.emitted("save")).toEqual([[{
            name: "Renamed",
            description: "desc"
        }]]));
    });

    it("emits close from the header close button", async () => {
        const wrapper = mountDrawer();

        await wrapper.find("button[type=button]").trigger("click");

        expect(wrapper.emitted("close")).toHaveLength(1);
    });

    it("locks the details once training has started", () => {
        const wrapper = mountDrawer({ modelPending: false });

        expect(wrapper.text()).toContain("you can no longer edit model details");
        expect(wrapper.find("[data-test=model-name]").attributes("disabled")).toBeDefined();
        expect(wrapper.find("[data-test=update-model-btn]").exists()).toBe(false);
    });

    it.each([
        ["an admin", {
            userId: "admin-1",
            permissions: ["CanAccessAdminPanel"]
        }, true],
        ["the project owner", {
            userId: "owner-1",
            permissions: ["CanCreateProjects"]
        }, true],
        ["a researcher not on the project", {
            userId: "other",
            permissions: ["CanCreateProjects"]
        }, false],
        ["a viewer", {
            userId: "owner-1",
            permissions: []
        }, false]
    ])("offers Delete Model to %s: %s", (_who, user, offered) => {
        expect(mountDrawer({}, user).find("[data-test=delete-model-btn]").exists()).toBe(offered);
    });

    it("deletes the model, returns to its project and confirms", async () => {
        vi.mocked(deleteModel).mockResolvedValueOnce(undefined as never);
        const wrapper = mountDrawer();

        await wrapper.find("[data-test=delete-model-btn]").trigger("click");
        await wrapper.find("[data-test=confirm-delete]").trigger("click");
        await flushPromises();

        expect(deleteModel).toHaveBeenCalledWith("/model/model-1");
        expect(router.push).toHaveBeenCalledWith({ path: "/project/proj-1" });
        expect(Snackbar.success).toHaveBeenCalledWith(expect.objectContaining({ title: "Model deleted" }));
        expect(wrapper.find("[data-test=confirm-open]").exists()).toBe(false);
    });

    it("reports a failed delete, closes the drawer and stays on the page", async () => {
        vi.mocked(deleteModel).mockRejectedValueOnce(new Error("boom"));
        const wrapper = mountDrawer();

        await wrapper.find("[data-test=delete-model-btn]").trigger("click");
        await wrapper.find("[data-test=confirm-delete]").trigger("click");
        await flushPromises();

        expect(Snackbar.error).toHaveBeenCalledWith({
            title: "Unable to delete this model",
            text: "The model \"Acme\" has not been deleted."
        });
        expect(useErrorStore().hasError).toBe(true);
        expect(wrapper.emitted("close")).toHaveLength(1);
        expect(router.push).not.toHaveBeenCalled();
        expect(wrapper.find("[data-test=confirm-open]").exists()).toBe(false);
    });
});
