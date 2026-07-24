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

// submitAction calls registerUser; stub it so the tests assert the payload
// (crucially the name/email identity) without issuing a real HTTP request.
const mockRegisterUser = vi.fn();

vi.mock("@/services/user-service", async (importOriginal) => {
    const actual = await importOriginal<typeof import("@/services/user-service")>();

    return {
        ...actual,
        registerUser: (...args: unknown[]) => mockRegisterUser(...args)
    };
});

vi.mock("@/utils/snackbar", () => ({
    Snackbar: {
        show: vi.fn(),
        error: vi.fn(),
        success: vi.fn(),
        warning: vi.fn()
    }
}));

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

    it("shows name + email pre-filled and read-only when enrolling from a request", () => {
        // The "Enroll" action on an access request opens this modal with the
        // requester's name + email fixed: pre-populated and non-editable.
        const component = mount(RegisterUserModal, {
            attachTo: document.body,
            props: {
                dialog: true,
                title: "Enroll User",
                roles: [roleA],
                initialName: "Pending Person",
                initialEmail: "pending@example.test"
            },
            global: {
                renderStubDefaultSlot: true,
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })]
            }
        });

        const nameInput = component.find("[data-test=\"name-field\"]");
        const emailInput = component.find("[data-test=\"email-field\"]");
        expect((nameInput.element as HTMLInputElement).value).toBe("Pending Person");
        expect((emailInput.element as HTMLInputElement).value).toBe("pending@example.test");
        // Locked: rendered as disabled inputs, not the editable AiInput.
        expect(nameInput.attributes("disabled")).toBeDefined();
        expect(emailInput.attributes("disabled")).toBeDefined();
        component.unmount();
    });

    it("keeps name + email editable in the plain register-user flow", () => {
        const component = mount(RegisterUserModal, {
            attachTo: document.body,
            props: {
                dialog: true,
                title: "Register User",
                roles: [roleA]
            },
            global: {
                renderStubDefaultSlot: true,
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })]
            }
        });

        // No initial identity -> the fields stay editable (not disabled).
        expect(component.find("[data-test=\"name-field\"]").attributes("disabled")).toBeUndefined();
        expect(component.find("[data-test=\"email-field\"]").attributes("disabled")).toBeUndefined();
        component.unmount();
    });

    describe("submitting the form", () => {
        beforeEach(() => {
            mockRegisterUser.mockReset();
        });

        // Mounts closed then opens, so the dialog watch fires and pre-fills the
        // form (load-bearing for the locked enrol flow, where the name/email
        // fields are read-only and only the watch populates the form values).
        const mountAndOpen = (props: Record<string, unknown>) => {
            const component = mount(RegisterUserModal, {
                attachTo: document.body,
                props: {
                    dialog: false,
                    title: "Register User",
                    roles: [roleA],
                    ...props
                },
                global: {
                    renderStubDefaultSlot: true,
                    plugins: [createTestingPinia({
                        createSpy: vi.fn,
                        stubActions: false
                    })]
                }
            });

            return component;
        };

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const setupStateOf = (component: any) => component.vm.$.setupState;

        // Sets the admin-chosen role via the same reactive path the Listbox
        // uses (selectedOption -> watch -> role field), then drives submit.
        const selectRole = async (setupState: { selectedOption: IOption }) => {
            setupState.selectedOption = {
                id: roleA.id,
                description: roleA.rolename
            };
            await nextTick();
        };

        it("submits the locked identity from props when enrolling", async () => {
            mockRegisterUser.mockResolvedValue({ email: "pending@example.test" });
            const component = mountAndOpen({
                title: "Enroll User",
                initialName: "Pending Person",
                initialEmail: "pending@example.test"
            });

            // Opening fires the watch that pre-fills name + email into the form
            // (load-bearing: the locked fields are read-only, so validation only
            // passes because the watch populated the form values).
            await component.setProps({ dialog: true });
            const setupState = setupStateOf(component);
            // Only organisation + role are admin-chosen; identity comes from props.
            setupState.setFieldValue("organisation", "Example Org");
            await selectRole(setupState);

            await setupState.submitAction();

            expect(mockRegisterUser).toHaveBeenCalledWith({
                name: "Pending Person",
                organisation: "Example Org",
                email: "pending@example.test",
                roles: [roleA.id]
            });
            component.unmount();
        });

        it("submits the typed identity in the plain register-user flow", async () => {
            mockRegisterUser.mockResolvedValue({ email: "typed@example.test" });
            const component = mountAndOpen({});

            await component.setProps({ dialog: true });
            const setupState = setupStateOf(component);
            setupState.setFieldValue("name", "Typed Name");
            setupState.setFieldValue("email", "typed@example.test");
            setupState.setFieldValue("organisation", "Typed Org");
            await selectRole(setupState);

            await setupState.submitAction();

            expect(mockRegisterUser).toHaveBeenCalledWith({
                name: "Typed Name",
                organisation: "Typed Org",
                email: "typed@example.test",
                roles: [roleA.id]
            });
            component.unmount();
        });

        it("does not call registerUser when required fields are missing", async () => {
            const component = mountAndOpen({});
            const setupState = setupStateOf(component);

            // No fields filled -> validation fails and the submit is a no-op.
            await setupState.submitAction();

            expect(mockRegisterUser).not.toHaveBeenCalled();
            component.unmount();
        });
    });
});
