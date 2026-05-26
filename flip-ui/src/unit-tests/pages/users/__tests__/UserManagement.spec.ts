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
import { flushPromises, mount, VueWrapper } from "@vue/test-utils";
import { beforeEach, describe, expect, it, test, vi } from "vitest";
import { ref } from "vue";

import UserManagement from "@/pages/admin/users.vue";

// Bypass swrv's process-wide DATA_CACHE: each useSWRV call re-invokes
// the fetcher fresh. Without this, mutations to objects returned by
// mockGetUsers (the "add role" test mutates user.roles by reference)
// leak into subsequent tests via swrv's cached resolved data.
vi.mock("swrv", () => {
    const useSWRV = (keyFn: () => string, fetcher?: (key: string) => unknown) => {
        const data = ref<unknown>(undefined);
        const error = ref<unknown>(null);
        if (fetcher) {
            Promise.resolve(fetcher(keyFn()))
                .then((d) => { data.value = d; })
                .catch((e) => { error.value = e; });
        }

        return {
            data,
            error,
            mutate: vi.fn(),
            isValidating: ref(false)
        };
    };

    return { default: useSWRV };
});

// The admin users page now exposes a "Reset MFA" action that fans out to
// the user-service helper. Mock the helper so we can assert on calls and
// drive success/error branches.
const mockResetUserMfa = vi.fn();
const mockGetUsers = vi.fn();
const mockGetRoles = vi.fn();
const mockUpdateUserDisabledState = vi.fn();
const mockUpdateUserProfile = vi.fn();
const mockUpdateUserRoles = vi.fn();

vi.mock("@/services/user-service", () => ({
    resetUserMfa: (...args: unknown[]) => mockResetUserMfa(...args),
    getUsers: (...args: unknown[]) => mockGetUsers(...args),
    updateUserDisabledState: (...args: unknown[]) => mockUpdateUserDisabledState(...args),
    updateUserProfile: (...args: unknown[]) => mockUpdateUserProfile(...args),
    updateUserRoles: (...args: unknown[]) => mockUpdateUserRoles(...args)
}));

vi.mock("@/services/role-service", () => ({ getRoles: (...args: unknown[]) => mockGetRoles(...args) }));

const mockRouteNotAllowed = vi.fn();
const mockViewProjects = vi.fn();

vi.mock("@/router", () => ({
    default: { push: vi.fn() },
    routeChange: {
        notAllowed: (...args: unknown[]) => mockRouteNotAllowed(...args),
        viewProjects: (...args: unknown[]) => mockViewProjects(...args)
    }
}));

const mockSnackbarShow = vi.fn();
const mockSnackbarError = vi.fn();
const mockSnackbarSuccess = vi.fn();

vi.mock("@/utils/snackbar", () => ({
    Snackbar: {
        show: (...args: unknown[]) => mockSnackbarShow(...args),
        error: (...args: unknown[]) => mockSnackbarError(...args),
        success: (...args: unknown[]) => mockSnackbarSuccess(...args)
    }
}));

// Authorised by default — tests that need the 403 branch override with
// mockResolvedValueOnce(false).
const mockCanAccessRoute = vi.fn().mockResolvedValue(true);
vi.mock("@/utils/route-validator", () => ({ canAccessRoute: (...args: unknown[]) => mockCanAccessRoute(...args) }));

const SAMPLE_USER = {
    id: "user-1",
    email: "user@example.com",
    name: "User Example",
    organisation: "Example Org",
    roles: [{
        id: "role-1",
        rolename: "admin",
        roledescription: "Administrator"
    }],
    isDisabled: false
};

function mountUserManagement(): VueWrapper {
    return mount(UserManagement, {
        global: {
            plugins: [
                createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })
            ],
            stubs: {
                // Transitions / popovers get in the way of synchronous
                // DOM assertions — flatten them so the action buttons
                // inside the panel are rendered immediately.
                AiCard: { template: "<div><slot /></div>" },
                AiSkeleton: true,
                AiPagination: true,
                AiLabel: true,
                AiConfirmModal: {
                    template:
                        "<div :data-test=\"$attrs['data-test-id']\" v-if=\"dialog\"><button :data-test=\"$attrs['continue-button-data-test']\" @click=\"continueAction && continueAction()\">{{ continueButtonText }}</button></div>",
                    props: ["dialog", "confirmationText", "continueButtonText", "closeButtonText", "continueAction"]
                },
                RegisterUserModal: true,
                Popover: { template: "<div><slot /></div>" },
                PopoverButton: { template: "<div><slot /></div>" },
                PopoverGroup: { template: "<div><slot /></div>" },
                PopoverPanel: { template: "<div><slot /></div>" },
                AiButton: {
                    // Inherit attrs (including data-test) so we can find
                    // the specific action buttons by data-test selector.
                    template: "<button :data-test=\"$attrs['data-test']\" @click=\"$emit('click', $event)\"><slot /></button>",
                    props: ["primary", "light", "text", "textSecondary", "block", "disabled", "loading"],
                    emits: ["click"]
                }
            }
        }
    });
}

describe("User Management", () => {
    beforeEach(() => {
        mockResetUserMfa.mockReset();
        mockGetUsers.mockReset();
        mockGetRoles.mockReset();
        mockUpdateUserDisabledState.mockReset();
        mockUpdateUserProfile.mockReset();
        mockUpdateUserRoles.mockReset();
        mockRouteNotAllowed.mockReset();
        mockViewProjects.mockReset();
        mockSnackbarShow.mockReset();
        mockSnackbarError.mockReset();
        mockSnackbarSuccess.mockReset();
        mockCanAccessRoute.mockReset();
        mockCanAccessRoute.mockResolvedValue(true);

        mockGetUsers.mockResolvedValue({
            data: [SAMPLE_USER],
            page: 1,
            totalPages: 1
        });
        mockGetRoles.mockResolvedValue({
            roles: [
                {
                    id: "role-1",
                    rolename: "admin",
                    roledescription: "Administrator"
                },
                {
                    id: "role-2",
                    rolename: "viewer",
                    roledescription: "View-only"
                }
            ]
        });
    });

    it("renders the component successfully", () => {
        const component = mountUserManagement();

        expect(component.exists()).toBe(true);
    });

    it("shows the Register User button", () => {
        const component = mountUserManagement();

        expect(component.find("[data-test='register-user-btn']").exists()).toBe(true);
    });

    describe("Route guard", () => {
        test("redirects to /projects when the user lacks CanManageUsers", async () => {
            mockCanAccessRoute.mockResolvedValueOnce(false);

            mountUserManagement();
            await flushPromises();

            expect(mockViewProjects).toHaveBeenCalledTimes(1);
        });

        test("stays on the page when the user has CanManageUsers", async () => {
            mountUserManagement();
            await flushPromises();

            expect(mockViewProjects).not.toHaveBeenCalled();
        });
    });

    describe("Reset MFA action", () => {
        async function mountAndSelectUser(): Promise<VueWrapper> {
            const wrapper = mountUserManagement();
            await flushPromises();
            // Select the sample user so the action panel renders.
            await wrapper.find("[data-test='user']").trigger("click");
            await flushPromises();

            return wrapper;
        }

        test("the 'Reset MFA' button is rendered in the actions panel", async () => {
            const wrapper = await mountAndSelectUser();

            expect(wrapper.find("[data-test='reset-mfa-btn']").exists()).toBe(true);
        });

        test("clicking 'Reset MFA' opens the confirmation dialog (does NOT call the API yet)", async () => {
            const wrapper = await mountAndSelectUser();

            await wrapper.find("[data-test='reset-mfa-btn']").trigger("click");
            await flushPromises();

            // The API should only be called after the user confirms.
            expect(mockResetUserMfa).not.toHaveBeenCalled();
        });

        test("confirming the dialog calls resetUserMfa and shows a success snackbar", async () => {
            mockResetUserMfa.mockResolvedValueOnce(undefined);
            const wrapper = await mountAndSelectUser();

            // Open the dialog, then click "continue" inside the confirm modal.
            await wrapper.find("[data-test='reset-mfa-btn']").trigger("click");
            await flushPromises();
            // All our stubbed AiConfirmModals render with the shared
            // continueButtonText — we use the Reset MFA text to find the right one.
            const confirmBtns = wrapper.findAll("button").filter(b => b.text() === "Reset MFA");
            // First one is the action-panel trigger (already clicked);
            // second is inside the confirm modal that just opened.
            const confirmTrigger = confirmBtns[confirmBtns.length - 1];
            await confirmTrigger.trigger("click");
            await flushPromises();

            expect(mockResetUserMfa).toHaveBeenCalledTimes(1);
            expect(mockResetUserMfa).toHaveBeenCalledWith(SAMPLE_USER.id);
            expect(mockSnackbarSuccess).toHaveBeenCalledTimes(1);
            expect(mockSnackbarSuccess.mock.calls[0][0]).toMatchObject({ title: "MFA reset" });
            expect(mockSnackbarError).not.toHaveBeenCalled();
        });

        test("resetUserMfa failure surfaces an error snackbar (no success toast)", async () => {
            mockResetUserMfa.mockRejectedValueOnce(new Error("Backend unavailable"));
            const wrapper = await mountAndSelectUser();

            await wrapper.find("[data-test='reset-mfa-btn']").trigger("click");
            await flushPromises();
            const confirmBtns = wrapper.findAll("button").filter(b => b.text() === "Reset MFA");
            await confirmBtns[confirmBtns.length - 1].trigger("click");
            await flushPromises();

            expect(mockResetUserMfa).toHaveBeenCalledTimes(1);
            expect(mockSnackbarError).toHaveBeenCalledTimes(1);
            expect(mockSnackbarError.mock.calls[0][0]).toMatchObject({ title: "MFA reset failed" });
            expect(mockSnackbarSuccess).not.toHaveBeenCalled();
        });

        test("resetMfa is a no-op when no user is selected", async () => {
            // Don't click any user — selectedUser stays undefined.
            const wrapper = mountUserManagement();
            await flushPromises();

            // With no selectedUser, the reset-mfa-btn is gated by the
            // v-if on selectedUser and should not render.
            expect(wrapper.find("[data-test='reset-mfa-btn']").exists()).toBe(false);
            expect(mockResetUserMfa).not.toHaveBeenCalled();
        });
    });

    describe("saveUser", () => {
        // Hand each test its own user so role mutations don't leak across tests.
        function freshUser() {
            return {
                id: "user-1",
                email: "user@example.com",
                name: "User Example",
                organisation: "Example Org",
                roles: [{
                    id: "role-1",
                    rolename: "admin",
                    roledescription: "Administrator"
                }],
                isDisabled: false
            };
        }

        // The role selector renders from a separate useSWRV /roles call;
        // needs its own flush after the user click before the radio exists.
        async function mountAndSelectViewerRole(): Promise<VueWrapper> {
            mockGetUsers.mockResolvedValue({
                data: [freshUser()],
                page: 1,
                totalPages: 1
            });
            const wrapper = mountUserManagement();
            await flushPromises();
            await flushPromises();
            await wrapper.find("[data-test='user']").trigger("click");
            await flushPromises();
            await flushPromises();
            await wrapper.find("[data-test='select-viewer-role'] input").setValue();

            return wrapper;
        }

        // The AiButton stub doesn't reflect the `disabled` prop onto the
        // underlying DOM element, so read the prop off the component
        // directly to verify the dirty-flag → disabled binding.
        function saveButtonDisabled(wrapper: VueWrapper): boolean {
            const btn = wrapper.findComponent("[data-test='save-user-btn']") as VueWrapper;

            return (btn.props() as Record<string, unknown>).disabled as boolean;
        }

        test("calls updateUserRoles, clears dirty, and shows a success snackbar on success", async () => {
            mockUpdateUserRoles.mockResolvedValueOnce([]);

            const wrapper = await mountAndSelectViewerRole();
            expect(saveButtonDisabled(wrapper)).toBe(false);

            await wrapper.find("[data-test='save-user-btn']").trigger("click");
            await flushPromises();

            expect(mockUpdateUserRoles).toHaveBeenCalledWith("user-1", ["role-2"]);
            expect(mockSnackbarSuccess).toHaveBeenCalledWith({
                text: "The user has been updated.",
                title: "User updated"
            });
            expect(mockSnackbarError).not.toHaveBeenCalled();
            expect(saveButtonDisabled(wrapper)).toBe(true);
        });

        test("shows an error snackbar and keeps dirty state when updateUserRoles rejects", async () => {
            mockUpdateUserRoles.mockRejectedValueOnce(new Error("API error"));

            const wrapper = await mountAndSelectViewerRole();
            await wrapper.find("[data-test='save-user-btn']").trigger("click");
            await flushPromises();

            expect(mockUpdateUserRoles).toHaveBeenCalledWith("user-1", ["role-2"]);
            expect(mockSnackbarError).toHaveBeenCalledWith({
                text: "The user could not be updated, please try again.",
                title: "Update failed"
            });
            expect(mockSnackbarSuccess).not.toHaveBeenCalled();
            expect(saveButtonDisabled(wrapper)).toBe(false);
        });
    });

    describe("User list & search", () => {
        const USERS = [
            {
                id: "u1",
                email: "amara.okonkwo@gstt.nhs.uk",
                name: "Amara Okonkwo",
                organisation: "Guy's & St Thomas'",
                roles: [{
                    id: "role-1",
                    rolename: "Admin",
                    roledescription: "Administrator"
                }],
                isDisabled: false
            },
            {
                id: "u2",
                email: "priya.raghavan@kcl.ac.uk",
                name: "Priya Raghavan",
                organisation: "King's College London",
                roles: [{
                    id: "role-2",
                    rolename: "Researcher",
                    roledescription: "Researcher"
                }],
                isDisabled: false
            },
            {
                id: "u3",
                email: "tomas.ribeiro@nhs.uk",
                name: "Tomas Ribeiro",
                organisation: "King's College Hospital",
                roles: [{
                    id: "role-3",
                    rolename: "Observer",
                    roledescription: "Observer"
                }],
                isDisabled: true
            }
        ];

        async function mountWithUsers(): Promise<VueWrapper> {
            mockGetUsers.mockResolvedValue({
                data: USERS,
                page: 1,
                pageSize: 20,
                totalPages: 1,
                totalRecords: 3
            });
            const wrapper = mountUserManagement();
            await flushPromises();

            return wrapper;
        }

        test("renders one row per user, each with a role-tinted avatar and badge", async () => {
            const wrapper = await mountWithUsers();

            expect(wrapper.findAll("[data-test='user']")).toHaveLength(3);
            expect(wrapper.findAll("[data-test='user'] [data-test='user-avatar']")).toHaveLength(3);
            expect(wrapper.findAll("[data-test='user'] [data-test='role-badge']")).toHaveLength(3);
        });

        test("typing in the search box filters the list by name", async () => {
            const wrapper = await mountWithUsers();

            await wrapper.find("[data-test='user-search']").setValue("priya");
            await flushPromises();

            const rows = wrapper.findAll("[data-test='user']");
            expect(rows).toHaveLength(1);
            expect(rows[0].text()).toContain("Priya Raghavan");
        });

        test("search also matches email, organisation, and role", async () => {
            const wrapper = await mountWithUsers();

            await wrapper.find("[data-test='user-search']").setValue("kcl.ac.uk");
            await flushPromises();
            expect(wrapper.findAll("[data-test='user']")).toHaveLength(1);

            await wrapper.find("[data-test='user-search']").setValue("King's College London");
            await flushPromises();
            expect(wrapper.findAll("[data-test='user']")).toHaveLength(1);

            await wrapper.find("[data-test='user-search']").setValue("researcher");
            await flushPromises();
            expect(wrapper.findAll("[data-test='user']")).toHaveLength(1);
        });

        test("shows an empty-state message when no user matches the search", async () => {
            const wrapper = await mountWithUsers();

            await wrapper.find("[data-test='user-search']").setValue("no-such-user");
            await flushPromises();

            expect(wrapper.findAll("[data-test='user']")).toHaveLength(0);
            expect(wrapper.text()).toContain("No users match your search.");
        });
    });
});
