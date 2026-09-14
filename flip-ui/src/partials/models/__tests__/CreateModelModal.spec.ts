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
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CreateModelModal from "@/partials/models/CreateModelModal.vue";
import { useModalsStore } from "@/store/modals";
import { Snackbar } from "@/utils/snackbar";

const mockCreateModel = vi.fn();

vi.mock("@/services/model-service", async (importOriginal) => {
    const actual = await importOriginal<typeof import("@/services/model-service")>();

    return {
        ...actual,
        createModel: (...args: unknown[]) => mockCreateModel(...args)
    };
});

vi.mock("@/router", () => ({ routeChange: { viewModel: vi.fn() } }));

vi.mock("@/utils/snackbar", () => ({
    Snackbar: {
        show: vi.fn(),
        error: vi.fn()
    }
}));

// The component falls back to route.params.projectId when no prop is passed; a shared
// mutable object lets each test choose whether that param exists.
const mockRoute = { params: {} as Record<string, string> };

vi.mock("vue-router", async (importOriginal) => {
    const actual = await importOriginal<typeof import("vue-router")>();

    return {
        ...actual,
        useRoute: () => mockRoute
    };
});

const stubs = {
    TransitionRoot: { template: "<div><slot /></div>" },
    Dialog: { template: "<div><slot /></div>" },
    DialogPanel: { template: "<div><slot /></div>" },
    DialogTitle: { template: "<div><slot /></div>" },
    TransitionChild: { template: "<div><slot /></div>" },
    // Bypass vee-validate: submit straight into submitForm with already-valid values. Declaring
    // `emits` is load-bearing — without it Vue also attaches the parent's @submit natively to the
    // root <form>, and every DOM submit would call submitForm twice.
    Form: {
        emits: ["submit"],
        template: "<form @submit.prevent=\"$emit('submit', values)\"><slot /></form>",
        data: () => ({
            values: {
                name: "Test model",
                description: "A model description"
            }
        })
    },
    "icon-mdi-close": { template: "<span>×</span>" }
};

const mountModal = (props: { open: boolean; projectId?: string }) =>
    mount(CreateModelModal, {
        props,
        global: {
            plugins: [createTestingPinia({
                createSpy: vi.fn,
                stubActions: false
            })],
            stubs
        }
    });

describe("Create Model Modal", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        mockRoute.params = {};
        mockCreateModel.mockResolvedValue({ id: "created-model-id" });
    });

    it("creates the model against the projectId prop, preferring it over the route param", async () => {
        mockRoute.params = { projectId: "route-project-id" };
        const component = mountModal({
            open: true,
            projectId: "prop-project-id"
        });

        await component.find("form").trigger("submit");
        await flushPromises();

        expect(mockCreateModel).toHaveBeenCalledWith("/model", {
            name: "Test model",
            description: "A model description",
            projectId: "prop-project-id"
        });
        expect(Snackbar.show).toHaveBeenCalledWith(expect.objectContaining({ type: "success" }));

        const { routeChange } = await import("@/router");
        expect(routeChange.viewModel).toHaveBeenCalledWith("prop-project-id", "created-model-id");
    });

    it("falls back to the route's projectId param when no prop is passed", async () => {
        mockRoute.params = { projectId: "route-project-id" };
        const component = mountModal({ open: true });

        await component.find("form").trigger("submit");
        await flushPromises();

        expect(mockCreateModel).toHaveBeenCalledWith("/model", expect.objectContaining({ projectId: "route-project-id" }));
    });

    it("refuses the submit with an error snackbar when no project is known at all", async () => {
        const component = mountModal({ open: true });

        await component.find("form").trigger("submit");
        await flushPromises();

        expect(mockCreateModel).not.toHaveBeenCalled();
        expect(Snackbar.error).toHaveBeenCalledWith(expect.objectContaining({ title: "No project selected" }));

        // The guard must reset formSubmitting, otherwise the button is dead for good:
        // a second submit reaching the guard again proves the flag was released.
        await component.find("form").trigger("submit");
        await flushPromises();

        expect(Snackbar.error).toHaveBeenCalledTimes(2);
    });

    it("ignores a second submit while the first is still in flight", async () => {
        let resolveCreate!: (value: { id: string }) => void;
        mockCreateModel.mockImplementation(() => new Promise((resolve) => {
            resolveCreate = resolve;
        }));
        const component = mountModal({
            open: true,
            projectId: "prop-project-id"
        });

        await component.find("form").trigger("submit");
        await component.find("form").trigger("submit");

        expect(mockCreateModel).toHaveBeenCalledTimes(1);

        resolveCreate({ id: "created-model-id" });
        await flushPromises();
    });

    it("shows an error snackbar and closes the modal when creation fails", async () => {
        mockCreateModel.mockRejectedValue(new Error("boom"));
        const component = mountModal({
            open: true,
            projectId: "prop-project-id"
        });

        await component.find("form").trigger("submit");
        await flushPromises();

        expect(Snackbar.show).toHaveBeenCalledWith(expect.objectContaining({ type: "error" }));

        const modalStore = useModalsStore();
        expect(modalStore.toggleCreateModel).toHaveBeenCalled();
    });

    it("closes the modal from the cancel button", async () => {
        const component = mountModal({
            open: true,
            projectId: "prop-project-id"
        });

        await component.find("[data-test='close-create-project-btn']").trigger("click");

        const modalStore = useModalsStore();
        expect(modalStore.toggleCreateModel).toHaveBeenCalledTimes(1);
        expect(mockCreateModel).not.toHaveBeenCalled();
    });
});
