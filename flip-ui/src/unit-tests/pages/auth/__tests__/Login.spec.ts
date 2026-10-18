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

import { createTestingPinia, TestingPinia } from "@pinia/testing";
import { flushPromises, mount, VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { makeMockAuthProvider, NO_CAPABILITIES } from "@/auth/__tests__/mock-provider";
import { AccountActionRequiredError, SignInStep } from "@/auth/provider";
import Login from "@/pages/auth/Login.vue";
import { useAuthStore } from "@/store/auth";

// Router is imported at module-scope by the page, so it has to be mocked
// before any Login import resolves. The spies here let us assert which
// page the login flow routes to given the signInStep returned by the store.
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
const mockSnackbarWarning = vi.fn();

vi.mock("@/utils/snackbar", () => ({
    Snackbar: {
        show: (...args: unknown[]) => mockSnackbarShow(...args),
        error: (...args: unknown[]) => mockSnackbarError(...args),
        warning: (...args: unknown[]) => mockSnackbarWarning(...args)
    }
}));

// The page reaches the provider only through the store (actions are
// stubbed by the testing pinia) and the `capabilities` getter, which reads
// the mocked provider below.
const authProvider = vi.hoisted(() => ({ current: null as unknown }));
vi.mock("@/auth", () => ({ getAuthProvider: () => authProvider.current }));

const win = window as unknown as Record<string, unknown>;

interface AuthStoreState {
    signInStep: string | null;
    user: unknown;
    mfaEnabled: boolean | null;
    mfaRequired: boolean | null;
}

// The pinia is created before the mount so a test can arm store actions
// (`hasSession`) and getters (`capabilities`) that run during setup /
// onBeforeMount.
function makePinia(authState: Partial<AuthStoreState> = {}): TestingPinia {
    return createTestingPinia({
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
    });
}

function mountLogin(pinia: TestingPinia = makePinia()): VueWrapper {
    return mount(Login, {
        global: {
            plugins: [pinia],
            stubs: {
                // emit the submit event with realistic creds; the page
                // doesn't care about schema validation when the Form is
                // stubbed, so we provide the values directly.
                Form: {
                    template:
                        // pragma: allowlist nextline secret
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
                "router-link": {
                    template: "<a :data-test=\"$attrs['data-test']\" :href=\"to\"><slot /></a>",
                    props: ["to"]
                }
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
        mockSnackbarWarning.mockReset();
        authProvider.current = makeMockAuthProvider();
    });

    afterEach(() => {
        delete win.KEYCLOAK_URL;
        delete win.KEYCLOAK_REALM;
        delete win.KEYCLOAK_CLIENT_ID;
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
        test("redirects to /projects when the store reports a live session", async () => {
            const pinia = makePinia();
            const authStore = useAuthStore(pinia);
            (authStore.hasSession as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(true);

            mountLogin(pinia);
            await flushPromises();

            expect(mockViewProjects).toHaveBeenCalledTimes(1);
        });

        test("stays on /auth/login when there is no session (mid-challenge or signed out)", async () => {
            // Regression guard: a stale challenge-only session used to count
            // as "signed in" and short-circuit, so "Back to log in" from any
            // mid-challenge page bounced back to the challenge. `hasSession`
            // is the provider's answer to exactly that question.
            const pinia = makePinia();
            const authStore = useAuthStore(pinia);
            (authStore.hasSession as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(false);

            mountLogin(pinia);
            await flushPromises();

            expect(mockViewProjects).not.toHaveBeenCalled();
        });

        test("swallows a hasSession failure and stays on the login page", async () => {
            const pinia = makePinia();
            const authStore = useAuthStore(pinia);
            (authStore.hasSession as unknown as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("No current user"));

            mountLogin(pinia);
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
                    authStore.signInStep = SignInStep.NEW_PASSWORD_REQUIRED;
                }
            );

            await wrapper.find("form").trigger("submit");
            await flushPromises();

            expect(authStore.signIn).toHaveBeenCalledWith({
                username: "user@example.com",
                password: "Password123!"  // pragma: allowlist secret
            });
            expect(mockNewPassword).toHaveBeenCalledTimes(1);
            expect(mockMfaSetup).not.toHaveBeenCalled();
            expect(mockMfaVerify).not.toHaveBeenCalled();
            expect(mockViewProjects).not.toHaveBeenCalled();
        });

        test("TOTP_SETUP routes to /auth/mfa-setup", async () => {
            const wrapper = mountLogin();
            await flushPromises();
            const authStore = useAuthStore();
            (authStore.signIn as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(
                async () => {
                    authStore.signInStep = SignInStep.TOTP_SETUP;
                }
            );

            await wrapper.find("form").trigger("submit");
            await flushPromises();

            expect(mockMfaSetup).toHaveBeenCalledTimes(1);
            expect(mockViewProjects).not.toHaveBeenCalled();
        });

        test("TOTP_CODE routes to /auth/mfa-verify", async () => {
            const wrapper = mountLogin();
            await flushPromises();
            const authStore = useAuthStore();
            (authStore.signIn as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(
                async () => {
                    authStore.signInStep = SignInStep.TOTP_CODE;
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
            // needsMfaEnrolment requires mfaRequired=true as well, so set
            // that explicitly — the default mfaRequired=null would prevent
            // the enrolment redirect from firing even with mfaEnabled=false.
            const wrapper = mountLogin(makePinia({
                mfaEnabled: false,
                mfaRequired: true
            }));
            await flushPromises();
            const authStore = useAuthStore();
            (authStore.signIn as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(
                async () => {
                    authStore.signInStep = SignInStep.DONE;
                    authStore.mfaEnabled = false;
                    authStore.mfaRequired = true;
                }
            );

            await wrapper.find("form").trigger("submit");
            await flushPromises();

            expect(mockMfaSetup).toHaveBeenCalledTimes(1);
            expect(mockViewProjects).not.toHaveBeenCalled();
        });

        test("default step + needsMfaEnrolment=true still routes to /projects when the backend cannot enrol in-app", async () => {
            authProvider.current = makeMockAuthProvider({
                backend: "keycloak",
                capabilities: NO_CAPABILITIES
            });
            win.KEYCLOAK_URL = "http://kc.test";
            win.KEYCLOAK_REALM = "flip";
            win.KEYCLOAK_CLIENT_ID = "flip-ui";
            const wrapper = mountLogin();
            await flushPromises();
            const authStore = useAuthStore();
            (authStore.signIn as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(
                async () => {
                    authStore.signInStep = SignInStep.DONE;
                    authStore.mfaEnabled = false;
                    authStore.mfaRequired = true;
                }
            );

            await wrapper.find("form").trigger("submit");
            await flushPromises();

            expect(mockMfaSetup).not.toHaveBeenCalled();
            expect(mockViewProjects).toHaveBeenCalledTimes(1);
        });

        test("default step + MFA enabled routes to /projects", async () => {
            const wrapper = mountLogin();
            await flushPromises();
            const authStore = useAuthStore();
            (authStore.signIn as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(
                async () => {
                    authStore.signInStep = SignInStep.DONE;
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
                    authStore.signInStep = SignInStep.DONE;
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

        test("AccountActionRequiredError shows a long-lived warning with a link to the provider's console", async () => {
            // Keycloak: the account has a required action pending (forced
            // password update, ...). The generic "check your details"
            // message would send the user retrying a correct password.
            const wrapper = mountLogin();
            await flushPromises();
            const authStore = useAuthStore();
            (authStore.signIn as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
                new AccountActionRequiredError("http://kc.test/realms/flip/account")
            );
            const windowOpen = vi.spyOn(window, "open").mockImplementation(() => null);

            await wrapper.find("form").trigger("submit");
            await flushPromises();

            expect(mockSnackbarShow).not.toHaveBeenCalled();
            expect(mockSnackbarWarning).toHaveBeenCalledTimes(1);
            const [payload, timeout] = mockSnackbarWarning.mock.calls[0] as [
                { text: string; actionText?: string; action?: () => void },
                number
            ];
            expect(payload.text).toContain("Finish setting up your account in Keycloak, then sign in again");
            expect(payload.actionText).toBe("Open Keycloak");
            expect(timeout).toBeGreaterThanOrEqual(60_000);

            payload.action?.();
            expect(windowOpen).toHaveBeenCalledWith("http://kc.test/realms/flip/account", "_blank", expect.stringContaining("noopener"));
            expect(mockViewProjects).not.toHaveBeenCalled();
            windowOpen.mockRestore();
        });
    });

    describe("forgot-password link", () => {
        test("is the in-app reset page when the backend supports it", async () => {
            const wrapper = mountLogin();
            await flushPromises();

            const link = wrapper.find("[data-test='forgot-password-link']");
            expect(link.attributes("href")).toBe("/auth/change-password");
            expect(link.attributes("target")).toBeUndefined();
        });

        test("opens the provider's own reset page in a new tab when the backend has no in-app flow", async () => {
            authProvider.current = makeMockAuthProvider({
                backend: "keycloak",
                capabilities: NO_CAPABILITIES
            });
            win.KEYCLOAK_URL = "http://kc.test";
            win.KEYCLOAK_REALM = "flip";
            win.KEYCLOAK_CLIENT_ID = "flip-ui";
            const wrapper = mountLogin();
            await flushPromises();

            const link = wrapper.find("[data-test='forgot-password-link']");
            expect(link.attributes("href")).toBe(
                "http://kc.test/realms/flip/login-actions/reset-credentials?client_id=flip-ui"
            );
            expect(link.attributes("target")).toBe("_blank");
            expect(link.attributes("rel")).toContain("noopener");
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
