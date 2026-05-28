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

import { mountComponent } from "@test/helper";
import { describe, expect, it, vi } from "vitest";

import EditModelDrawer from "@/partials/models/EditModelDrawer.vue";

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

vi.mock("@/services/model-service", () => ({ deleteModel: vi.fn() }));

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

        expect(html).toContain("Training for this model will also be stopped");
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
