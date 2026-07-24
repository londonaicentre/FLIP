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

import Login from "@/pages/auth/Login.vue";
import { useAuthStore } from "@/store/auth";

// Router is imported at module-scope by the page, so it has to be mocked
// before any Login import resolves. The spies here let us assert which
// page the login flow routes to given the signInStep returned by Cognito.
const mockGotoLogin = vi.fn();
const mockViewProjects = vi.fn();
const mockNewPassword = vi.fn();
const mockMfaSetup = vi.fn();
const mockMfaVerify = vi.fn();
const mockAccessRequest = vi.fn();

vi.mock("@/router", () => ({
    default: { push: vi.fn() },
    routeChange: {
        gotoLogin: (...args: unknown[]) => mockGotoLogin(...args),
        viewProjects: (...args: unknown[]) => mockViewProjects(...args),
        newPassword: (...args: unknown[]) => mockNewPassword(...args),
        mfaSetup: (...args: unknown[]) => mockMfaSetup(...args),
        mfaVerify: (...args: unknown[]) => mockMfaVerify(...args),
        accessRequest: (...args: unknown[]) => mockAccessRequest(...args)
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

// `fetchAuthSession` is called from onBeforeMount to decide whether to
// short-circuit straight to /projects. Default is "no session" — each
// suite re-arms it if we want to exercise the short-circuit path.
const mockFetchAuthSession = vi.fn();
vi.mock("aws-amplify/auth", () => ({ fetchAuthSession: (...args: unknown[]) => mockFetchAuthSession(...args) }));

interface AuthStoreState {
    signInStep: string | null;
    user: unknown;
    mfaEnabled: boolean | null;
    mfaRequired: boolean | null;
    needsMfaEnrolment: boolean;
}

function mountLogin(authState: Partial<AuthStoreState> = {}): VueWrapper {
    return mount(Login, {
        global: {
            plugins: [
                createTestingPinia({
                    createSpy: vi.fn,
                    initialState: {
                        auth: {
                            signInStep: null,
                            user: null,
                            mfaEnabled: null,
                            mfaRequired: null,
                            ...authState
                        }
                    }
                })
            ],
            stubs: {
                // emit the submit event with realistic creds; the page
                // doesn't care about schema validation when the Form is
                // stubbed, so we provide the values directly.
                Form: {
                    template:
                        "<form @submit.prevent=\"$emit('submit', { email: 'user@example.com', password: 'Password123!' })\"><slot /></form>",
                    inheritAttrs: false,
                    emits: ["submit"]
                },
                AiInput: {
                    template: "<div><slot name=\"labelRight\" /></div>",
                    props: ["name", "type", "label", "preIcon", "inputProps"]
                },
                AiButton: {
                    template: "<button :data-test=\"$attrs['data-test']\" @click=\"$emit('click')\"><slot /></button>",
                    props: ["primary", "clear", "block", "loading", "inputProps"],
                    emits: ["click"]
                },
                "router-link": true
            }
        }
    });
}

describe("Login page", () => {
    beforeEach(() => {
        mockGotoLogin.mockReset();
        mockViewProjects.mockReset();
        mockNewPassword.mockReset();
        mockMfaSetup.mockReset();
        mockMfaVerify.mockReset();
        mockAccessRequest.mockReset();
        mockSnackbarShow.mockReset();
        mockSnackbarError.mockReset();
        mockFetchAuthSession.mockReset();
        // Default: no tokens — stay on login.
        mockFetchAuthSession.mockResolvedValue({ tokens: undefined });
    });

    test("mounts successfully and renders the Log In button", async () => {
        const wrapper = mountLogin();
        await flushPromises();

        expect(wrapper.exists()).toBe(true);
        expect(wrapper.find("[data-test='login-btn']").exists()).toBe(true);
    });

    test("labels the credential fields with valid autocomplete tokens", async () => {
        const wrapper = mountLogin();
        await flushPromises();

        // "password" is not a valid autocomplete token and breaks the
        // accessibility tree; login credentials are username/current-password.
        // AiInput is stubbed here, so the attribute falls through to the stub
        // root — AiInput's own spec covers forwarding onto the real <input>.
        expect(wrapper.find("[data-test='username']").attributes("autocomplete")).toBe("username");
        expect(wrapper.find("[data-test='password']").attributes("autocomplete")).toBe("current-password");
    });

    describe("onBeforeMount short-circuit", () => {
        test("redirects to /projects when the user already has an access token", async () => {
            mockFetchAuthSession.mockResolvedValueOnce({
                tokens: {
                    accessToken: {
                        payload: {},
                        toString: () => "tok"
                    }
                }
            });

            mountLogin();
            await flushPromises();

            expect(mockViewProjects).toHaveBeenCalledTimes(1);
        });

        test("stays on /auth/login when the session has no access token (mid-challenge)", async () => {
            // Regression guard: fetchAuthSession can resolve with a stale
            // challenge payload that has no tokens. The page must NOT
            // short-circuit in that case, otherwise "Back to log in" from
            // any mid-challenge page would bounce back to the challenge.
            mockFetchAuthSession.mockResolvedValueOnce({ tokens: undefined });

            mountLogin();
            await flushPromises();

            expect(mockViewProjects).not.toHaveBeenCalled();
        });

        test("swallows fetchAuthSession errors and stays on the login page", async () => {
            mockFetchAuthSession.mockRejectedValueOnce(new Error("No current user"));

            mountLogin();
            await flushPromises();

            expect(mockViewProjects).not.toHaveBeenCalled();
        });
    });

    describe("submit routing based on next sign-in step", () => {
        test("NEW_PASSWORD_REQUIRED routes to /auth/new-password", async () => {
            const wrapper = mountLogin();
            await flushPromises();
            const authStore = useAuthStore();
            (authStore.signIn as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(
                async () => {
                    authStore.signInStep = "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED";
                }
            );

            await wrapper.find("form").trigger("submit");
            await flushPromises();

            expect(authStore.signIn).toHaveBeenCalledWith({
                username: "user@example.com",
                password: "Password123!"
            });
            expect(mockNewPassword).toHaveBeenCalledTimes(1);
            expect(mockMfaSetup).not.toHaveBeenCalled();
            expect(mockMfaVerify).not.toHaveBeenCalled();
            expect(mockViewProjects).not.toHaveBeenCalled();
        });

        test("CONTINUE_SIGN_IN_WITH_TOTP_SETUP routes to /auth/mfa-setup", async () => {
            const wrapper = mountLogin();
            await flushPromises();
            const authStore = useAuthStore();
            (authStore.signIn as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(
                async () => {
                    authStore.signInStep = "CONTINUE_SIGN_IN_WITH_TOTP_SETUP";
                }
            );

            await wrapper.find("form").trigger("submit");
            await flushPromises();

            expect(mockMfaSetup).toHaveBeenCalledTimes(1);
            expect(mockViewProjects).not.toHaveBeenCalled();
        });

        test("CONFIRM_SIGN_IN_WITH_TOTP_CODE routes to /auth/mfa-verify", async () => {
            const wrapper = mountLogin();
            await flushPromises();
            const authStore = useAuthStore();
            (authStore.signIn as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(
                async () => {
                    authStore.signInStep = "CONFIRM_SIGN_IN_WITH_TOTP_CODE";
                }
            );

            await wrapper.find("form").trigger("submit");
            await flushPromises();

            expect(mockMfaVerify).toHaveBeenCalledTimes(1);
            expect(mockViewProjects).not.toHaveBeenCalled();
        });

        test("default step + needsMfaEnrolment=true routes to /auth/mfa-setup", async () => {
            // The needsMfaEnrolment getter comes from the real pinia
            // definition; we tweak the store state so it evaluates true.
            // needsMfaEnrolment now requires mfaRequired=true as well, so
            // set that explicitly — the default mfaRequired=null would
            // prevent the enrolment redirect from firing even with
            // mfaEnabled=false.
            const wrapper = mountLogin({
                mfaEnabled: false,
                mfaRequired: true
            });
            await flushPromises();
            const authStore = useAuthStore();
            (authStore.signIn as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(
                async () => {
                    authStore.signInStep = "DONE";
                    authStore.mfaEnabled = false;
                    authStore.mfaRequired = true;
                }
            );

            await wrapper.find("form").trigger("submit");
            await flushPromises();

            expect(mockMfaSetup).toHaveBeenCalledTimes(1);
            expect(mockViewProjects).not.toHaveBeenCalled();
        });

        test("default step + MFA enabled routes to /projects", async () => {
            const wrapper = mountLogin();
            await flushPromises();
            const authStore = useAuthStore();
            (authStore.signIn as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(
                async () => {
                    authStore.signInStep = "DONE";
                    authStore.mfaEnabled = true;
                    authStore.mfaRequired = true;
                    authStore.user = {
                        username: "u",
                        userId: "u",
                        attributes: {
                            sub: "s",
                            email: "u@e.com"
                        },
                        permissions: []
                    };
                }
            );

            await wrapper.find("form").trigger("submit");
            await flushPromises();

            expect(mockViewProjects).toHaveBeenCalledTimes(1);
            expect(mockMfaSetup).not.toHaveBeenCalled();
        });

        test("dev bypass: mfaRequired=false + mfaEnabled=false still routes to /projects", async () => {
            // Mirror of the router-guard case: when the backend says MFA
            // is not required for this environment (dev), an unenrolled
            // user logs straight into the app — no /auth/mfa-setup detour.
            const wrapper = mountLogin();
            await flushPromises();
            const authStore = useAuthStore();
            (authStore.signIn as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(
                async () => {
                    authStore.signInStep = "DONE";
                    authStore.mfaEnabled = false;
                    authStore.mfaRequired = false;
                    authStore.user = {
                        username: "u",
                        userId: "u",
                        attributes: {
                            sub: "s",
                            email: "u@e.com"
                        },
                        permissions: []
                    };
                }
            );

            await wrapper.find("form").trigger("submit");
            await flushPromises();

            expect(mockViewProjects).toHaveBeenCalledTimes(1);
            expect(mockMfaSetup).not.toHaveBeenCalled();
        });

        test("signIn failure shows an error snackbar and does NOT navigate", async () => {
            const wrapper = mountLogin();
            await flushPromises();
            const authStore = useAuthStore();
            (authStore.signIn as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
                new Error("NotAuthorizedException: Incorrect username or password.")
            );

            await wrapper.find("form").trigger("submit");
            await flushPromises();

            expect(mockSnackbarShow).toHaveBeenCalledTimes(1);
            const [payload] = mockSnackbarShow.mock.calls[0];
            expect(payload).toMatchObject({
                type: "error",
                title: "Error"
            });
            expect(mockViewProjects).not.toHaveBeenCalled();
            expect(mockMfaSetup).not.toHaveBeenCalled();
            expect(mockMfaVerify).not.toHaveBeenCalled();
            expect(mockNewPassword).not.toHaveBeenCalled();
        });
    });

    describe("request-access button", () => {
        test("clicking 'Request access' calls routeChange.accessRequest", async () => {
            const wrapper = mountLogin();
            await flushPromises();

            await wrapper.find("[data-test='request-access-btn']").trigger("click");

            expect(mockAccessRequest).toHaveBeenCalledTimes(1);
        });
    });
});
