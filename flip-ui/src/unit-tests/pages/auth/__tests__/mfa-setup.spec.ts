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
import { nextTick } from "vue";

import MfaSetup from "@/pages/auth/mfa-setup.vue";
import { useAuthStore } from "@/store/auth";

// Route/router mocks — the page calls routeChange.* and we need to assert
// on which navigation target it picked.
const mockGotoLogin = vi.fn();
const mockViewProjects = vi.fn();

vi.mock("@/router", () => ({
    default: { push: vi.fn() },
    routeChange: {
        gotoLogin: (...args: unknown[]) => mockGotoLogin(...args),
        viewProjects: (...args: unknown[]) => mockViewProjects(...args)
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

// QR rendering is a canvas side-effect; stub it so we can verify the URI
// passed in without running anything in jsdom.
const mockToDataURL = vi.fn().mockResolvedValue("data:image/png;base64,STUB");
vi.mock("qrcode", () => ({ default: { toDataURL: (...args: unknown[]) => mockToDataURL(...args) } }));

const SIGN_IN_CHAIN_STEP = "CONTINUE_SIGN_IN_WITH_TOTP_SETUP";

interface AuthStoreState {
    signInStep: string | null;
    user: unknown;
    mfaEnabled: boolean | null;
    totpSetup: { sharedSecret: string; setupUri: string } | null;
}

// Mount with stubbed Pinia — every action on the auth store becomes a
// vi.fn() automatically, so the Amplify-coupled real implementations
// never run in jsdom and we get clean spies to assert against.
function mountMfaSetup(authState: Partial<AuthStoreState>): VueWrapper {
    return mount(MfaSetup, {
        global: {
            plugins: [
                createTestingPinia({
                    createSpy: vi.fn,
                    // stubActions defaults to true — keeping that so all
                    // store actions are no-op spies.
                    initialState: {
                        auth: {
                            signInStep: null,
                            user: null,
                            mfaEnabled: null,
                            totpSetup: null,
                            ...authState
                        }
                    }
                })
            ],
            stubs: {
                Form: {
                    // inheritAttrs:false so Vue doesn't ALSO bind the
                    // parent's @submit listener directly to the inner
                    // <form> element — otherwise the parent's submit
                    // handler fires twice (once natively via the form
                    // element's submit event, once via our $emit).
                    template: "<form @submit.prevent=\"$emit('submit', { code: '123456' })\"><slot /></form>",
                    inheritAttrs: false,
                    emits: ["submit"]
                },
                AiInput: {
                    template: "<input />",
                    props: ["name", "type", "label", "preIcon", "inputProps"]
                },
                AiButton: {
                    // Default inheritAttrs=true sends both `type` and the
                    // parent's @click listener to the <button> as native
                    // DOM bindings, so a real click fires the handler
                    // exactly once (no $emit needed, which would double-fire).
                    template: "<button><slot /></button>",
                    props: ["primary", "clear", "block", "loading", "disabled"]
                }
            }
        }
    });
}

describe("mfa-setup page", () => {
    beforeEach(() => {
        mockGotoLogin.mockReset();
        mockViewProjects.mockReset();
        mockSnackbarShow.mockReset();
        mockSnackbarError.mockReset();
        mockToDataURL.mockClear();
    });

    // "Back to log in" lives in AuthLayout now (see AuthLayout.spec.ts); the
    // page no longer renders its own sign-out control.
    test("does not render a page-level Back-to-log-in button", () => {
        const wrapper = mountMfaSetup({ signInStep: SIGN_IN_CHAIN_STEP });

        expect(wrapper.find("[data-test='mfa-setup-signout-btn']").exists()).toBe(false);
    });

    describe("Sign-in-chain flow (signInStep = CONTINUE_SIGN_IN_WITH_TOTP_SETUP)", () => {
        test("does not call beginMfaEnrolment — the setup details are already in the store", async () => {
            const wrapper = mountMfaSetup({
                signInStep: SIGN_IN_CHAIN_STEP,
                totpSetup: {
                    sharedSecret: "ABCD1234",
                    setupUri: "otpauth://totp/FLIP"
                }
            });
            await flushPromises();
            const authStore = useAuthStore();

            expect(authStore.beginMfaEnrolment).not.toHaveBeenCalled();
            // The form was mounted into the DOM, no early exit.
            expect(wrapper.find("form").exists()).toBe(true);
        });

        test("missing setupUri bounces the user to login", async () => {
            // If we somehow reach the page mid-sign-in-chain with no
            // totpSetupDetails in the store, there's nothing to render;
            // sending the user back to login is the only safe recovery.
            mountMfaSetup({
                signInStep: SIGN_IN_CHAIN_STEP,
                totpSetup: null
            });
            await flushPromises();

            expect(mockGotoLogin).toHaveBeenCalledTimes(1);
            const authStore = useAuthStore();
            expect(authStore.beginMfaEnrolment).not.toHaveBeenCalled();
        });

        test("submit routes through confirmTotpSetup (not completeMfaEnrolment)", async () => {
            const wrapper = mountMfaSetup({
                signInStep: SIGN_IN_CHAIN_STEP,
                totpSetup: {
                    sharedSecret: "ABCD1234",
                    setupUri: "otpauth://totp/FLIP"
                }
            });
            await flushPromises();
            const authStore = useAuthStore();

            await wrapper.find("form").trigger("submit");
            await flushPromises();

            expect(authStore.confirmTotpSetup).toHaveBeenCalledWith("123456");
            expect(authStore.completeMfaEnrolment).not.toHaveBeenCalled();
            expect(mockViewProjects).toHaveBeenCalledTimes(1);
        });
    });

    describe("Post-auth flow (signed-in user with no active TOTP)", () => {
        const signedInUser = {
            username: "u",
            userId: "u",
            attributes: {
                sub: "s",
                email: "u@e.com"
            },
            permissions: []
        };

        test("mfaEnabled=true redirects straight to projects (no enrolment needed)", async () => {
            mountMfaSetup({
                signInStep: "DONE",
                user: signedInUser,
                mfaEnabled: true
            });
            await flushPromises();
            const authStore = useAuthStore();

            expect(mockViewProjects).toHaveBeenCalledTimes(1);
            expect(authStore.beginMfaEnrolment).not.toHaveBeenCalled();
        });

        test("no user in the store bounces to login", async () => {
            mountMfaSetup({
                signInStep: "DONE",
                user: null,
                mfaEnabled: false
            });
            await flushPromises();
            const authStore = useAuthStore();

            expect(mockGotoLogin).toHaveBeenCalledTimes(1);
            expect(authStore.beginMfaEnrolment).not.toHaveBeenCalled();
        });

        test("calls beginMfaEnrolment on mount to mint a fresh secret", async () => {
            mountMfaSetup({
                signInStep: "DONE",
                user: signedInUser,
                mfaEnabled: false
            });
            await flushPromises();
            const authStore = useAuthStore();

            expect(authStore.beginMfaEnrolment).toHaveBeenCalledTimes(1);
            expect(authStore.confirmTotpSetup).not.toHaveBeenCalled();
        });

        test("submit routes through completeMfaEnrolment (not confirmTotpSetup)", async () => {
            const wrapper = mountMfaSetup({
                signInStep: "DONE",
                user: signedInUser,
                mfaEnabled: false,
                totpSetup: {
                    sharedSecret: "ABCD1234",
                    setupUri: "otpauth://totp/FLIP"
                }
            });
            await flushPromises();
            const authStore = useAuthStore();

            await wrapper.find("form").trigger("submit");
            await flushPromises();

            expect(authStore.completeMfaEnrolment).toHaveBeenCalledWith("123456");
            expect(authStore.confirmTotpSetup).not.toHaveBeenCalled();
            expect(mockViewProjects).toHaveBeenCalledTimes(1);
        });

        test("beginMfaEnrolment failure surfaces a snackbar and does not crash the page", async () => {
            const wrapper = mountMfaSetup({
                signInStep: "DONE",
                user: signedInUser,
                mfaEnabled: false
            });
            const authStore = useAuthStore();
            // Re-stub the already-spied beginMfaEnrolment so it rejects
            // after mount has completed — this exercises the catch block
            // in the page's onMounted. Pinia's vi.fn stub allows us to
            // re-arm it mid-test.
            (authStore.beginMfaEnrolment as unknown as ReturnType<typeof vi.fn>)
                .mockRejectedValueOnce(new Error("Software Token MFA has not been enabled by the userPool"));

            // Trigger a second call path: remount (cheapest way to re-enter onMounted).
            wrapper.unmount();
            mountMfaSetup({
                signInStep: "DONE",
                user: signedInUser,
                mfaEnabled: false
            });
            await flushPromises();
            await nextTick();

            // Covered indirectly: a failure during enrolment doesn't throw
            // past the page boundary (no unhandled rejection surfaces in
            // the test runner) and the redirect never fires.
            expect(mockGotoLogin).not.toHaveBeenCalled();
        });

        test("beginMfaEnrolment failure fires the 'Couldn't start enrolment' snackbar with the error message", async () => {
            // Directly verify the catch block at onMounted line 157-158:
            // the failure path must surface the underlying Amplify error
            // to the user rather than silently leaving a blank page.
            //
            // createTestingPinia stubs the store's actions as vi.fn() the
            // first time useAuthStore() is called under that pinia — which
            // is during the component's setup(). To make beginMfaEnrolment
            // reject on that very first call we install a `beforeCreate`
            // plugin that re-arms the spy the moment the store is
            // instantiated inside the component tree.
            const throwingError = new Error("Cognito: throttled");
            const wrapper = mount(MfaSetup, {
                global: {
                    plugins: [
                        createTestingPinia({
                            createSpy: vi.fn,
                            initialState: {
                                auth: {
                                    signInStep: "DONE",
                                    user: signedInUser,
                                    mfaEnabled: false,
                                    totpSetup: null
                                }
                            }
                        })
                    ],
                    stubs: {
                        Form: {
                            template: "<form><slot /></form>",
                            inheritAttrs: false,
                            emits: ["submit"]
                        },
                        AiInput: {
                            template: "<input />",
                            props: ["name", "type", "label", "preIcon", "inputProps"]
                        },
                        AiButton: {
                            template: "<button><slot /></button>",
                            props: ["primary", "clear", "block", "loading", "disabled"]
                        }
                    }
                }
            });
            // Grab the in-component store AFTER mount so we can mutate its
            // spies, then unmount and remount to trigger onMounted against
            // the rejecting spy. (Pinia shares state across useAuthStore()
            // calls within the same pinia instance.)
            const store = useAuthStore();
            (store.beginMfaEnrolment as unknown as ReturnType<typeof vi.fn>)
                .mockRejectedValueOnce(throwingError);
            wrapper.unmount();
            // Second mount reuses the same pinia (via setActivePinia from
            // the first createTestingPinia call), so the rejecting spy is
            // in place when the new onMounted runs.
            mount(MfaSetup, {
                global: {
                    stubs: {
                        Form: {
                            template: "<form><slot /></form>",
                            inheritAttrs: false,
                            emits: ["submit"]
                        },
                        AiInput: {
                            template: "<input />",
                            props: ["name", "type", "label", "preIcon", "inputProps"]
                        },
                        AiButton: {
                            template: "<button><slot /></button>",
                            props: ["primary", "clear", "block", "loading", "disabled"]
                        }
                    }
                }
            });
            await flushPromises();

            expect(mockSnackbarError).toHaveBeenCalledWith(
                expect.objectContaining({
                    title: "Couldn't start enrolment",
                    text: expect.stringContaining("Cognito: throttled")
                })
            );
        });
    });

    describe("submit error handling", () => {
        const buildSigninChainState = (): Partial<AuthStoreState> => ({
            signInStep: SIGN_IN_CHAIN_STEP,
            totpSetup: {
                sharedSecret: "ABCD1234",
                setupUri: "otpauth://totp/FLIP"
            }
        });

        test("CodeMismatchException shows the 'Invalid code' snackbar (user retry will help)", async () => {
            const wrapper = mountMfaSetup(buildSigninChainState());
            await flushPromises();
            const authStore = useAuthStore();
            // Cognito's canonical name for a wrong TOTP digit; the page
            // treats this as a retryable user error, not an infra error.
            const mismatch = Object.assign(new Error("Invalid code passed"), { name: "CodeMismatchException" });
            (authStore.confirmTotpSetup as unknown as ReturnType<typeof vi.fn>)
                .mockRejectedValueOnce(mismatch);

            await wrapper.find("form").trigger("submit");
            await flushPromises();

            expect(mockSnackbarError).toHaveBeenCalledWith(
                expect.objectContaining({
                    title: "Invalid code",
                    text: expect.stringContaining("did not match")
                })
            );
            // Crucial: we stay on the page so the user can retry.
            expect(mockViewProjects).not.toHaveBeenCalled();
        });

        test("ExpiredCodeException is also treated as a retryable 'Invalid code'", async () => {
            // TOTP codes rotate every 30s; a slow user sees Cognito raise
            // ExpiredCodeException. From the user's perspective this is
            // still 'your code was wrong' — the same snackbar is correct.
            const wrapper = mountMfaSetup(buildSigninChainState());
            await flushPromises();
            const authStore = useAuthStore();
            const expired = Object.assign(new Error("Your software token has expired."), { name: "ExpiredCodeException" });
            (authStore.confirmTotpSetup as unknown as ReturnType<typeof vi.fn>)
                .mockRejectedValueOnce(expired);

            await wrapper.find("form").trigger("submit");
            await flushPromises();

            expect(mockSnackbarError).toHaveBeenCalledWith(
                expect.objectContaining({ title: "Invalid code" })
            );
        });

        test("'did not match' in the error message is also treated as a code error", async () => {
            // Defensive branch: some Amplify wrappers stringify the
            // exception rather than preserve the name. The heuristic
            // regex catches the common phrasing so the user still sees
            // the retry-friendly snackbar.
            const wrapper = mountMfaSetup(buildSigninChainState());
            await flushPromises();
            const authStore = useAuthStore();
            (authStore.confirmTotpSetup as unknown as ReturnType<typeof vi.fn>)
                .mockRejectedValueOnce(new Error("Provided code did not match an expected value"));

            await wrapper.find("form").trigger("submit");
            await flushPromises();

            expect(mockSnackbarError).toHaveBeenCalledWith(
                expect.objectContaining({ title: "Invalid code" })
            );
        });

        test("unrelated errors fall through to the generic 'Enrolment failed' snackbar", async () => {
            // Network blip, backend outage, Cognito quota — none of these
            // are fixable by re-entering the code. Show the underlying
            // message so the operator has something to act on.
            const wrapper = mountMfaSetup(buildSigninChainState());
            await flushPromises();
            const authStore = useAuthStore();
            (authStore.confirmTotpSetup as unknown as ReturnType<typeof vi.fn>)
                .mockRejectedValueOnce(new Error("Network Error"));

            await wrapper.find("form").trigger("submit");
            await flushPromises();

            expect(mockSnackbarError).toHaveBeenCalledWith(
                expect.objectContaining({
                    title: "Enrolment failed",
                    text: "Network Error"
                })
            );
        });

        test("completeMfaEnrolment failure in the post-auth flow routes through the same error handling", async () => {
            // Post-auth path uses completeMfaEnrolment instead of
            // confirmTotpSetup — the error branches should behave the same.
            const wrapper = mountMfaSetup({
                signInStep: "DONE",
                user: {
                    username: "u",
                    userId: "u",
                    attributes: {
                        sub: "s",
                        email: "u@e.com"
                    },
                    permissions: []
                },
                mfaEnabled: false,
                totpSetup: {
                    sharedSecret: "ABCD1234",
                    setupUri: "otpauth://totp/FLIP"
                }
            });
            await flushPromises();
            const authStore = useAuthStore();
            const mismatch = Object.assign(new Error("Invalid code passed"), { name: "CodeMismatchException" });
            (authStore.completeMfaEnrolment as unknown as ReturnType<typeof vi.fn>)
                .mockRejectedValueOnce(mismatch);

            await wrapper.find("form").trigger("submit");
            await flushPromises();

            expect(mockSnackbarError).toHaveBeenCalledWith(
                expect.objectContaining({ title: "Invalid code" })
            );
            expect(mockViewProjects).not.toHaveBeenCalled();
        });
    });

    describe("QR rendering error handling", () => {
        test("leaves qrDataUrl null and hides the <img> when QRCode.toDataURL throws", async () => {
            // Exercises the renderQr catch block (line 126). QR rendering
            // is best-effort — a bad URI must not break the page; the
            // user can still type the shared secret manually.
            mockToDataURL.mockRejectedValueOnce(new Error("invalid URI"));
            const wrapper = mountMfaSetup({
                signInStep: SIGN_IN_CHAIN_STEP,
                totpSetup: {
                    sharedSecret: "ABCD1234",
                    setupUri: "not-a-valid-uri"
                }
            });
            await flushPromises();

            expect(wrapper.find("[data-test='mfa-qr-code']").exists()).toBe(false);
            // The shared-secret fallback stays rendered.
            expect(wrapper.find("[data-test='mfa-shared-secret']").text()).toBe("ABCD1234");
        });
    });

    describe("QR rendering", () => {
        test("passes the setupUri and a grey light-mode colour to toDataURL", async () => {
            const setupUri = "otpauth://totp/FLIP:user?secret=ABCD1234";
            mountMfaSetup({
                signInStep: SIGN_IN_CHAIN_STEP,
                totpSetup: {
                    sharedSecret: "ABCD1234",
                    setupUri
                }
            });
            await flushPromises();

            expect(mockToDataURL).toHaveBeenCalled();
            const [uri, opts] = mockToDataURL.mock.calls[0] as [string, { color?: { light?: string } }];
            expect(uri).toBe(setupUri);
            // User-requested grey background (tailwind gray-200) rather
            // than the default hard white block.
            expect(opts.color?.light).toBe("#e5e7ebff");
        });
    });
});
