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
import { beforeEach, describe, expect, test, vi } from "vitest";

import NewPassword from "@/pages/auth/new-password.vue";
import { useAuthStore } from "@/store/auth";

const mockGotoLogin = vi.fn();
const mockViewProjects = vi.fn();
const mockMfaSetup = vi.fn();

vi.mock("@/router", () => ({
    default: { push: vi.fn() },
    routeChange: {
        gotoLogin: (...args: unknown[]) => mockGotoLogin(...args),
        viewProjects: (...args: unknown[]) => mockViewProjects(...args),
        mfaSetup: (...args: unknown[]) => mockMfaSetup(...args)
    }
}));

const mockSnackbarShow = vi.fn();
const mockSnackbarError = vi.fn();

vi.mock("@/utils/snackbar", () => ({
    Snackbar: {
        show: (...args: unknown[]) => mockSnackbarShow(...args),
        error: (...args: unknown[]) => mockSnackbarError(...args)
    }
}));

// The onBeforeMount guard delegates to isUserUnconfirmedCheck — stub it
// so we can force each branch (unconfirmed → stay, else → redirect).
const mockIsUserUnconfirmedCheck = vi.fn();
vi.mock("@/utils/auth", () => ({ isUserUnconfirmedCheck: (...args: unknown[]) => mockIsUserUnconfirmedCheck(...args) }));

interface AuthStoreState {
    signInStep: string | null;
    user: unknown;
    mfaEnabled: boolean | null;
    mfaRequired: boolean | null;
}

function mountNewPassword(
    authState: Partial<AuthStoreState> = {},
    unconfirmed = true
): VueWrapper {
    mockIsUserUnconfirmedCheck.mockResolvedValue(unconfirmed);

    return mount(NewPassword, {
        global: {
            plugins: [
                createTestingPinia({
                    createSpy: vi.fn,
                    initialState: {
                        auth: {
                            signInStep: "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED",
                            user: null,
                            mfaEnabled: null,
                            mfaRequired: null,
                            ...authState
                        }
                    }
                })
            ],
            stubs: {
                Form: {
                    template:
                        "<form @submit.prevent=\"$emit('submit', { password: 'NewPassw0rd!', passwordConfirmation: 'NewPassw0rd!' })\"><slot /></form>",
                    inheritAttrs: false,
                    emits: ["submit"]
                },
                AiInput: {
                    template: "<input />",
                    props: ["name", "type", "label", "preIcon"]
                },
                AiButton: {
                    template: "<button :data-test=\"$attrs['data-test']\" @click=\"$emit('click')\"><slot /></button>",
                    props: ["primary", "loading"],
                    emits: ["click"]
                }
            }
        }
    });
}

describe("new-password page", () => {
    beforeEach(() => {
        mockGotoLogin.mockReset();
        mockViewProjects.mockReset();
        mockMfaSetup.mockReset();
        mockSnackbarShow.mockReset();
        mockSnackbarError.mockReset();
        mockIsUserUnconfirmedCheck.mockReset();
    });

    describe("onBeforeMount guard", () => {
        test("renders the password form when the user is in the unconfirmed state", async () => {
            const wrapper = mountNewPassword({}, true);
            await flushPromises();

            expect(mockViewProjects).not.toHaveBeenCalled();
            expect(wrapper.find("[data-test='new-password']").exists()).toBe(true);
            expect(wrapper.find("[data-test='change-password-btn']").exists()).toBe(true);
        });

        test("bounces to /projects when the user is already confirmed", async () => {
            mountNewPassword({}, false);
            await flushPromises();

            expect(mockViewProjects).toHaveBeenCalledTimes(1);
        });
    });

    describe("submit flow", () => {
        test("success → CONTINUE_SIGN_IN_WITH_TOTP_SETUP routes to /auth/mfa-setup", async () => {
            const wrapper = mountNewPassword({}, true);
            await flushPromises();
            const authStore = useAuthStore();
            (authStore.changePassword as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(
                async () => {
                    authStore.signInStep = "CONTINUE_SIGN_IN_WITH_TOTP_SETUP";
                }
            );

            await wrapper.find("form").trigger("submit");
            await flushPromises();

            expect(authStore.changePassword).toHaveBeenCalledWith("NewPassw0rd!");
            expect(mockMfaSetup).toHaveBeenCalledTimes(1);
            // Success screen should NOT be shown when we chained into
            // MFA setup — the user still has to finish the chain.
            expect(wrapper.find("[data-test='password-changed-message']").exists()).toBe(false);
        });

        test("success → needsMfaEnrolment also routes to /auth/mfa-setup", async () => {
            const wrapper = mountNewPassword({}, true);
            await flushPromises();
            const authStore = useAuthStore();
            (authStore.changePassword as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(
                async () => {
                    // `needsMfaEnrolment` getter is true when signInStep=DONE AND
                    // mfaRequired=true AND mfaEnabled=false — the stag/prod case
                    // where the password change cleared the challenge chain but
                    // the user still has to enrol TOTP.
                    authStore.signInStep = "DONE";
                    authStore.mfaEnabled = false;
                    authStore.mfaRequired = true;
                }
            );

            await wrapper.find("form").trigger("submit");
            await flushPromises();

            expect(mockMfaSetup).toHaveBeenCalledTimes(1);
            expect(wrapper.find("[data-test='password-changed-message']").exists()).toBe(false);
        });

        test("success → neither MFA branch: shows 'Your password has been changed' screen", async () => {
            const wrapper = mountNewPassword({}, true);
            await flushPromises();
            const authStore = useAuthStore();
            (authStore.changePassword as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(
                async () => {
                    // MFA already active + sign-in chain cleared
                    authStore.signInStep = "DONE";
                    authStore.mfaEnabled = true;
                    authStore.mfaRequired = true;
                }
            );

            await wrapper.find("form").trigger("submit");
            await flushPromises();

            expect(mockMfaSetup).not.toHaveBeenCalled();
            expect(wrapper.find("[data-test='password-changed-message']").exists()).toBe(true);
        });

        test("changePassword failure shows an error snackbar and hides the success screen", async () => {
            const wrapper = mountNewPassword({}, true);
            await flushPromises();
            const authStore = useAuthStore();
            (authStore.changePassword as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
                new Error("InvalidPasswordException: Password does not conform to policy.")
            );

            await wrapper.find("form").trigger("submit");
            await flushPromises();

            expect(mockSnackbarShow).toHaveBeenCalledTimes(1);
            expect(mockSnackbarShow.mock.calls[0][0]).toMatchObject({
                type: "error",
                title: "Error"
            });
            expect(wrapper.find("[data-test='password-changed-message']").exists()).toBe(false);
            expect(mockMfaSetup).not.toHaveBeenCalled();
        });
    });

    describe("success screen", () => {
        test("clicking 'Log in' goes back to the login page", async () => {
            const wrapper = mountNewPassword({}, true);
            await flushPromises();
            const authStore = useAuthStore();
            (authStore.changePassword as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(
                async () => {
                    authStore.signInStep = "DONE";
                    authStore.mfaEnabled = true;
                }
            );
            await wrapper.find("form").trigger("submit");
            await flushPromises();

            // The new-password page reuses `data-test='login-btn'` for
            // the "Log in" button on its success screen.
            await wrapper.find("[data-test='login-btn']").trigger("click");
            await flushPromises();

            expect(mockGotoLogin).toHaveBeenCalledTimes(1);
        });
    });
});
