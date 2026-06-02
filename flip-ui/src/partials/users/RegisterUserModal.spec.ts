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
import { nextTick } from "vue";

import { IOption } from "@/components/AiSelect/interfaces";
import RegisterUserModal from "@/partials/users/RegisterUserModal.vue";
import { IRole } from "@/services/role-service";

const roleA: IRole = {
    id: "1",
    rolename: "Admin",
    roledescription: "Admin role"
};
const roleB: IRole = {
    id: "2",
    rolename: "Researcher",
    roledescription: "Researcher role"
};

const mountModal = (roles: IRole[] = [roleA, roleB]) =>
    mount(RegisterUserModal, {
        props: {
            dialog: true,
            title: "Register User",
            roles
        },
        global: {
            plugins: [createTestingPinia({
                createSpy: vi.fn,
                stubActions: false
            })]
        }
    });

describe("Register User Modal", () => {
    it("renders the component successfully", () => {
        const component = mountModal();
        expect(component.exists()).toBe(true);
    });

    it("does not use the multi-select chip widget for role selection", () => {
        // The global test setup stubs Headlessui's Dialog/Transition components
        // to a self-closing stub, which would hide the modal body. Render their
        // default slots so we can introspect the role widget inside.
        const component = mount(RegisterUserModal, {
            attachTo: document.body,
            props: {
                dialog: true,
                title: "Register User",
                roles: [roleA, roleB]
            },
            global: {
                renderStubDefaultSlot: true,
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })]
            }
        });
        // AiChipSelect tags its trigger with data-test="chip-select"; the new
        // single-select tags its trigger with data-test="role-select".
        expect(component.find("[data-test=\"chip-select\"]").exists()).toBe(false);
        expect(component.find("[data-test=\"role-select\"]").exists()).toBe(true);
        component.unmount();
    });

    it("replaces (not appends) the role when a different option is picked", async () => {
        const component = mountModal();
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const setupState = (component.vm as any).$.setupState;

        const optionA: IOption = {
            id: roleA.id,
            description: roleA.rolename
        };
        const optionB: IOption = {
            id: roleB.id,
            description: roleB.rolename
        };

        setupState.selectedOption = optionA;
        await nextTick();
        // useField returns a FieldContext; `.value` is the inner Ref, `.value.value` unwraps it.
        expect(setupState.role.value.value).toBe(roleA.id);

        setupState.selectedOption = optionB;
        await nextTick();
        // Single-select semantic: latest pick replaces the previous one.
        expect(setupState.role.value.value).toBe(roleB.id);
    });
});
