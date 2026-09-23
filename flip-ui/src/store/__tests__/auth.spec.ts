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

import { createPinia, setActivePinia } from "pinia";

import { makeMockAuthProvider, NO_CAPABILITIES, resetMockAuthProvider } from "@/auth/__tests__/mock-provider";
import { AuthError, SignInStep } from "@/auth/provider";
import { getMfaStatus, getUserPermissions } from "@/services/user-service";
import { useAuthStore } from "@/store/auth";
import { leaveToLogin, stashPostSignOutNotice } from "@/utils/session-teardown";
import { Snackbar } from "@/utils/snackbar";

// The store talks to the identity provider only through the `@/auth`
// seam; the provider is a bag of spies. Backend specifics (SRP, the
// UserAlreadyAuthenticated retry, the token race) are the providers' own
// specs under src/auth/__tests__.
const provider = vi.hoisted(() => ({ current: null as unknown }));
vi.mock("@/auth", () => ({ getAuthProvider: () => provider.current }));

vi.mock("@/services/user-service", () => ({
    getMfaStatus: vi.fn(),
    getUserPermissions: vi.fn()
}));

// Stub the router so nothing imported transitively instantiates a real
// router with generated pages.
vi.mock("@/router", () => ({
    default: { push: vi.fn() },
    routeChange: { gotoLogin: vi.fn() }
}));

// signOut ends the session by discarding the document (see
// utils/session-teardown.ts). jsdom cannot navigate, so the helper is
// stubbed and the tests assert that it — not a router push — is what
// signOut calls, and that notices are queued for the reload rather than
// shown into a page that is about to be unloaded.
vi.mock("@/utils/session-teardown", () => ({
    leaveToLogin: vi.fn(),
    stashPostSignOutNotice: vi.fn()
}));

vi.mock("@/utils/snackbar", () => ({
    Snackbar: {
        show: vi.fn(),
        error: vi.fn(),
        success: vi.fn(),
        warning: vi.fn()
    }
}));

const IDENTITY = {
    sub: "id",
    email: "e@f.com",
    username: "u"
};

const STORE_USER = {
    username: "u",
    userId: "id",
    attributes: {
        sub: "id",
        email: "e@f.com"
    },
    permissions: [] as string[]
};

describe("authStore", () => {
    let store: ReturnType<typeof useAuthStore>;
    const auth = makeMockAuthProvider();

    beforeEach(() => {
        setActivePinia(createPinia());
        store = useAuthStore();
        resetMockAuthProvider(auth);
        provider.current = auth;
        vi.mocked(getMfaStatus).mockReset();
        vi.mocked(getUserPermissions).mockReset();
        vi.mocked(leaveToLogin).mockReset();
        vi.mocked(stashPostSignOutNotice).mockReset();
        vi.mocked(Snackbar.error).mockReset();
        vi.mocked(Snackbar.show).mockReset();
        // Default: a signed-in identity is readable. Tests that exercise a
        // failing hydrate re-arm this.
        auth.getUser.mockResolvedValue(IDENTITY);
    });

    describe("initial state & getters", () => {
        it("initialises with nulls", () => {
            expect(store.user).toBeNull();
            expect(store.signInStep).toBeNull();
            expect(store.totpSetup).toBeNull();
            expect(store.mfaEnabled).toBeNull();
        });

        it("getUser returns the current user", () => {
            expect(store.getUser).toBeNull();
            store.user = { ...STORE_USER };
            expect(store.getUser).toEqual(store.user);
        });

        it("capabilities come from the configured provider", () => {
            expect(store.capabilities).toBe(auth.capabilities);

            // The provider is a per-page singleton, so the getter is a plain
            // (cached) computed: a different backend means a fresh store.
            provider.current = makeMockAuthProvider({
                backend: "keycloak",
                capabilities: NO_CAPABILITIES
            });
            setActivePinia(createPinia());
            expect(useAuthStore().capabilities).toEqual(NO_CAPABILITIES);
        });

        it("confirmedUser is true once challenges clear AND (env is MFA-off OR TOTP is active)", () => {
            expect(store.confirmedUser).toBe(false);

            store.signInStep = SignInStep.DONE;
            store.user = { ...STORE_USER };
            // Stag/prod path: mfaRequired=true forces mfaEnabled=true gate.
            store.mfaRequired = true;
            store.mfaEnabled = true;
            expect(store.confirmedUser).toBe(true);

            store.mfaEnabled = false;
            expect(store.confirmedUser).toBe(false);

            store.mfaEnabled = null;
            expect(store.confirmedUser).toBe(false);

            // Dev path: mfaRequired=false bypasses the mfaEnabled check
            // entirely — users who never enrolled still count as confirmed.
            store.mfaRequired = false;
            store.mfaEnabled = false;
            expect(store.confirmedUser).toBe(true);
        });

        it("needsMfaEnrolment is true only when DONE + mfaRequired=true + mfaEnabled=false", () => {
            expect(store.needsMfaEnrolment).toBe(false);

            store.signInStep = SignInStep.DONE;
            store.mfaRequired = true;
            store.mfaEnabled = false;
            expect(store.needsMfaEnrolment).toBe(true);

            store.mfaEnabled = true;
            expect(store.needsMfaEnrolment).toBe(false);

            store.mfaEnabled = null;
            expect(store.needsMfaEnrolment).toBe(false);

            // Dev bypass: even with mfaEnabled=false, an environment
            // that doesn't require MFA must NOT trigger enrolment.
            store.mfaRequired = false;
            store.mfaEnabled = false;
            expect(store.needsMfaEnrolment).toBe(false);
        });
    });

    describe("hydrate", () => {
        it("with mfa enabled, populates user with permissions via backend", async () => {
            vi.mocked(getMfaStatus).mockResolvedValue({
                enabled: true,
                required: true
            });
            vi.mocked(getUserPermissions).mockResolvedValue({ permissions: ["CanManageUsers"] });

            await store.hydrate();

            expect(store.signInStep).toBe(SignInStep.DONE);
            expect(store.mfaEnabled).toBe(true);
            expect(store.user).toEqual({
                ...STORE_USER,
                permissions: ["CanManageUsers"]
            });
            // Permissions are keyed on the provider subject, whatever backend issued it.
            expect(getUserPermissions).toHaveBeenCalledWith("id");
            expect(getMfaStatus).toHaveBeenCalledTimes(1);
        });

        it("defaults permissions to empty array when backend omits them", async () => {
            vi.mocked(getMfaStatus).mockResolvedValue({
                enabled: true,
                required: true
            });
            // getUserPermissions returns an object with no permissions key
            vi.mocked(getUserPermissions).mockResolvedValue({} as never);

            await store.hydrate();

            expect(store.user?.permissions).toEqual([]);
        });

        it("with mfa disabled, skips permissions fetch and leaves permissions empty", async () => {
            vi.mocked(getMfaStatus).mockResolvedValue({
                enabled: false,
                required: true
            });

            await store.hydrate();

            expect(store.signInStep).toBe(SignInStep.DONE);
            expect(store.mfaEnabled).toBe(false);
            expect(store.user).toEqual(STORE_USER);
            expect(getUserPermissions).not.toHaveBeenCalled();
        });

        it("accepts a known MFA state and skips the backend call", async () => {
            vi.mocked(getUserPermissions).mockResolvedValue({ permissions: [] });

            await store.hydrate({
                enabled: true,
                required: true
            });

            expect(getMfaStatus).not.toHaveBeenCalled();
            expect(store.mfaEnabled).toBe(true);
            expect(store.mfaRequired).toBe(true);
        });

        it("still fetches permissions when MFA is not enabled but this environment doesn't require it", async () => {
            // Dev bypass: a user who never enrolled TOTP in an environment
            // with ENFORCE_MFA=false still has full API access, so the
            // store should populate real permissions instead of the
            // identity-only placeholder reserved for the MFA-blocked case.
            vi.mocked(getMfaStatus).mockResolvedValue({
                enabled: false,
                required: false
            });
            vi.mocked(getUserPermissions).mockResolvedValue({ permissions: ["CanManageUsers"] });

            await store.hydrate();

            expect(store.mfaEnabled).toBe(false);
            expect(store.mfaRequired).toBe(false);
            expect(store.user?.permissions).toEqual(["CanManageUsers"]);
            expect(getUserPermissions).toHaveBeenCalledWith("id");
        });

        it("propagates a getUser failure (no session) untouched", async () => {
            auth.getUser.mockRejectedValue(new Error("The user is not authenticated"));
            vi.mocked(getMfaStatus).mockResolvedValue({
                enabled: true,
                required: true
            });

            await expect(store.hydrate()).rejects.toThrow("not authenticated");
            expect(store.user).toBeNull();
        });
    });

    describe("fetchInfo / finaliseSignIn", () => {
        it("both delegate to hydrate", async () => {
            vi.mocked(getMfaStatus).mockResolvedValue({
                enabled: false,
                required: true
            });

            await store.fetchInfo();
            expect(store.signInStep).toBe(SignInStep.DONE);
            expect(store.mfaEnabled).toBe(false);

            vi.mocked(getMfaStatus).mockResolvedValue({
                enabled: true,
                required: true
            });
            vi.mocked(getUserPermissions).mockResolvedValue({ permissions: [] });

            await store.finaliseSignIn();
            expect(store.mfaEnabled).toBe(true);
        });
    });

    describe("hasSession", () => {
        it("asks the provider", async () => {
            auth.hasSession.mockResolvedValue(true);
            await expect(store.hasSession()).resolves.toBe(true);

            auth.hasSession.mockResolvedValue(false);
            await expect(store.hasSession()).resolves.toBe(false);
        });
    });

    describe("signIn", () => {
        it("resets state, hands the credentials to the provider, then hydrates on DONE", async () => {
            // Pre-set state that must be cleared before sign-in runs.
            store.user = {
                ...STORE_USER,
                username: "stale"
            };
            store.signInStep = SignInStep.DONE;
            store.mfaEnabled = true;
            auth.signIn.mockResolvedValue({ step: SignInStep.DONE });
            vi.mocked(getMfaStatus).mockResolvedValue({
                enabled: true,
                required: true
            });
            vi.mocked(getUserPermissions).mockResolvedValue({ permissions: [] });

            await store.signIn({
                username: "u",
                password: "p"
            });

            expect(auth.signIn).toHaveBeenCalledWith("u", "p");
            expect(store.signInStep).toBe(SignInStep.DONE);
            expect(store.mfaEnabled).toBe(true);
            expect(store.user?.username).toBe("u");
        });

        it("captures TOTP setup details when the provider demands first-time enrolment", async () => {
            auth.signIn.mockResolvedValue({
                step: SignInStep.TOTP_SETUP,
                totpSetup: {
                    sharedSecret: "ABCD",  // pragma: allowlist secret
                    setupUri: "otpauth://totp/FLIP:u?secret=ABCD"
                }
            });

            await store.signIn({
                username: "u",
                password: "p"
            });

            expect(store.signInStep).toBe(SignInStep.TOTP_SETUP);
            expect(store.totpSetup).toEqual({
                sharedSecret: "ABCD",  // pragma: allowlist secret
                setupUri: "otpauth://totp/FLIP:u?secret=ABCD"
            });
            // Did NOT hydrate — we're mid-challenge.
            expect(auth.getUser).not.toHaveBeenCalled();
        });

        it("returns early for the new-password challenge without hydrating", async () => {
            auth.signIn.mockResolvedValue({ step: SignInStep.NEW_PASSWORD_REQUIRED });

            await store.signIn({
                username: "u",
                password: "p"
            });

            expect(store.signInStep).toBe(SignInStep.NEW_PASSWORD_REQUIRED);
            expect(auth.getUser).not.toHaveBeenCalled();
        });

        it("returns early for the TOTP-code challenge without hydrating", async () => {
            auth.signIn.mockResolvedValue({ step: SignInStep.TOTP_CODE });

            await store.signIn({
                username: "u",
                password: "p"
            });

            expect(store.signInStep).toBe(SignInStep.TOTP_CODE);
            expect(auth.getUser).not.toHaveBeenCalled();
        });

        it("propagates provider failures (wrong password, missing tokens) without hydrating", async () => {
            auth.signIn.mockRejectedValue(new AuthError("INVALID_CREDENTIALS", "Invalid user credentials"));

            await expect(store.signIn({
                username: "u",
                password: "p"
            })).rejects.toMatchObject({ code: "INVALID_CREDENTIALS" });

            expect(store.signInStep).toBeNull();
            expect(auth.getUser).not.toHaveBeenCalled();
        });

        it("rethrows post-signIn hydrate failures so Login.vue surfaces them", async () => {
            // The provider accepted the creds but the follow-up backend call
            // failed. The store must log the underlying error and rethrow so
            // the login page can tell the user something went wrong —
            // silently resolving here lets a broken session masquerade as
            // success and the next route-guarded call 401s.
            auth.signIn.mockResolvedValue({ step: SignInStep.DONE });
            vi.mocked(getMfaStatus).mockRejectedValue(
                new Error("Request failed with status code 401")
            );
            const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});

            await expect(
                store.signIn({
                    username: "u",
                    password: "p"
                })
            ).rejects.toThrow("Request failed with status code 401");

            expect(consoleSpy).toHaveBeenCalledWith(
                "Post-signIn hydrate failed:",
                expect.objectContaining({ message: "Request failed with status code 401" }),
                expect.any(Object)
            );
            consoleSpy.mockRestore();
        });
    });

    describe("changePassword", () => {
        it("hydrates after a successful password change", async () => {
            auth.confirmNewPassword.mockResolvedValue({ step: SignInStep.DONE });
            vi.mocked(getMfaStatus).mockResolvedValue({
                enabled: false,
                required: true
            });

            await store.changePassword("newPassword123!");

            expect(auth.confirmNewPassword).toHaveBeenCalledWith("newPassword123!");
            expect(store.signInStep).toBe(SignInStep.DONE);
            expect(store.mfaEnabled).toBe(false);
        });

        it("captures TOTP setup details when next step is MFA setup", async () => {
            auth.confirmNewPassword.mockResolvedValue({
                step: SignInStep.TOTP_SETUP,
                totpSetup: {
                    sharedSecret: "XYZ",  // pragma: allowlist secret
                    setupUri: "otpauth://totp/FLIP:u?secret=XYZ"
                }
            });

            await store.changePassword("newPassword123!");

            expect(store.signInStep).toBe(SignInStep.TOTP_SETUP);
            expect(store.totpSetup?.sharedSecret).toBe("XYZ");
            expect(auth.getUser).not.toHaveBeenCalled();
        });
    });

    describe("confirmTotpChallenge", () => {
        it("hydrates with known-true MFA when the challenge clears", async () => {
            auth.confirmTotpChallenge.mockResolvedValue({ step: SignInStep.DONE });
            vi.mocked(getUserPermissions).mockResolvedValue({ permissions: [] });

            await store.confirmTotpChallenge("123456");

            expect(auth.confirmTotpChallenge).toHaveBeenCalledWith("123456");
            // Did NOT call getMfaStatus — hydrate({enabled:true}) short-circuits it.
            expect(getMfaStatus).not.toHaveBeenCalled();
            expect(store.signInStep).toBe(SignInStep.DONE);
            expect(store.mfaEnabled).toBe(true);
        });

        it("propagates a wrong code without touching state", async () => {
            const mismatch = Object.assign(new Error("Code mismatch"), { name: "CodeMismatchException" });
            auth.confirmTotpChallenge.mockRejectedValue(mismatch);

            await expect(store.confirmTotpChallenge("000000")).rejects.toBe(mismatch);
            expect(store.signInStep).toBeNull();
            expect(auth.getUser).not.toHaveBeenCalled();
        });

        it("swallows post-success hydrate failures, leaves mfaEnabled=null and notifies the user", async () => {
            // Without a user-facing signal, a transient hydrate failure right
            // after a valid TOTP code leaves the user thinking sign-in worked
            // while every subsequent API call 401s under `mfaEnabled=null`.
            auth.confirmTotpChallenge.mockResolvedValue({ step: SignInStep.DONE });
            auth.getUser.mockRejectedValue(new Error("Network Error"));
            const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});

            await expect(store.confirmTotpChallenge("123456")).resolves.toBeUndefined();

            expect(store.signInStep).toBe(SignInStep.DONE);
            expect(store.mfaEnabled).toBeNull();
            expect(consoleSpy).toHaveBeenCalled();
            expect(Snackbar.show).toHaveBeenCalledWith(
                expect.objectContaining({ type: "warning" })
            );
            consoleSpy.mockRestore();
        });
    });

    describe("confirmTotpSetup", () => {
        it("clears totpSetup then hydrates with known-true MFA", async () => {
            store.totpSetup = {
                sharedSecret: "ABC",
                setupUri: "otpauth://..."
            };
            auth.confirmTotpSetup.mockResolvedValue({ step: SignInStep.DONE });
            vi.mocked(getUserPermissions).mockResolvedValue({ permissions: [] });

            await store.confirmTotpSetup("123456");

            expect(auth.confirmTotpSetup).toHaveBeenCalledWith("123456");
            expect(store.totpSetup).toBeNull();
            expect(store.mfaEnabled).toBe(true);
            expect(store.signInStep).toBe(SignInStep.DONE);
            expect(getMfaStatus).not.toHaveBeenCalled();
        });

        it("keeps the setup secret when the code is rejected so the user can retry against the same QR", async () => {
            store.totpSetup = {
                sharedSecret: "ABC",
                setupUri: "otpauth://..."
            };
            const mismatch = Object.assign(new Error("Invalid code"), { name: "CodeMismatchException" });
            auth.confirmTotpSetup.mockRejectedValue(mismatch);

            await expect(store.confirmTotpSetup("000000")).rejects.toBe(mismatch);

            expect(store.totpSetup?.sharedSecret).toBe("ABC");
            expect(auth.getUser).not.toHaveBeenCalled();
        });

        it("on MFA_PREFERENCE_FAILED: clears the secret, resets state, does NOT mark MFA enabled, rethrows", async () => {
            // The provider accepted the verification code but the preference
            // didn't stick. Painting `mfaEnabled=true` here would let the
            // user into the app where every API call 403s under the
            // app-gate. The store rethrows so the calling page (mfa-setup)
            // keeps the user on the form; the page is responsible for the
            // user-facing snackbar so we don't double-notify.
            store.totpSetup = {
                sharedSecret: "ABC",
                setupUri: "otpauth://..."
            };
            auth.confirmTotpSetup.mockRejectedValue(new AuthError("MFA_PREFERENCE_FAILED", "Cognito boom"));
            const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});

            await expect(store.confirmTotpSetup("123456")).rejects.toThrow("Cognito boom");

            expect(consoleSpy).toHaveBeenCalledWith(
                "Failed to set MFA preference post-setup:",
                expect.any(Error)
            );
            expect(store.mfaEnabled).toBeNull();
            expect(store.signInStep).toBe(SignInStep.DONE);
            expect(store.totpSetup).toBeNull();
            // hydrate must not run after a fatal preference failure —
            // forcing a fresh authoritative read happens on the next
            // navigation via the router guard.
            expect(auth.getUser).not.toHaveBeenCalled();
            expect(getMfaStatus).not.toHaveBeenCalled();
            // Snackbar is the page's job, not the store's.
            expect(Snackbar.error).not.toHaveBeenCalled();
            consoleSpy.mockRestore();
        });

        it("swallows hydrate failure and leaves mfaEnabled=null for router guard", async () => {
            auth.confirmTotpSetup.mockResolvedValue({ step: SignInStep.DONE });
            auth.getUser.mockRejectedValue(new Error("Network"));
            const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});

            await expect(store.confirmTotpSetup("123456")).resolves.toBeUndefined();

            expect(consoleSpy).toHaveBeenCalledWith(
                "Failed to hydrate user post-MFA setup:",
                expect.any(Error)
            );
            expect(store.signInStep).toBe(SignInStep.DONE);
            expect(store.mfaEnabled).toBeNull();
            consoleSpy.mockRestore();
        });
    });

    describe("beginMfaEnrolment", () => {
        it("records the secret the provider mints, labelled with the signed-in email", async () => {
            store.user = { ...STORE_USER };
            auth.setUpTotp.mockResolvedValue({
                sharedSecret: "SEC",  // pragma: allowlist secret
                setupUri: "otpauth://totp/FLIP:e@f.com?secret=SEC"
            });

            await store.beginMfaEnrolment();

            expect(auth.setUpTotp).toHaveBeenCalledWith("e@f.com");
            expect(store.totpSetup).toEqual({
                sharedSecret: "SEC",  // pragma: allowlist secret
                setupUri: "otpauth://totp/FLIP:e@f.com?secret=SEC"
            });
        });

        it("passes no label when there is no user in the store", async () => {
            auth.setUpTotp.mockResolvedValue({
                sharedSecret: "",
                setupUri: "otpauth://totp/FLIP"
            });

            await store.beginMfaEnrolment();

            expect(auth.setUpTotp).toHaveBeenCalledWith(undefined);
            expect(store.totpSetup?.sharedSecret).toBe("");
        });
    });

    describe("completeMfaEnrolment", () => {
        it("verifies the code, clears setup, and hydrates known-true", async () => {
            store.totpSetup = {
                sharedSecret: "ABC",
                setupUri: "otpauth://..."
            };
            auth.verifyTotpSetup.mockResolvedValue(undefined);
            vi.mocked(getUserPermissions).mockResolvedValue({ permissions: [] });

            await store.completeMfaEnrolment("123456");

            expect(auth.verifyTotpSetup).toHaveBeenCalledWith("123456");
            expect(store.totpSetup).toBeNull();
            expect(store.mfaEnabled).toBe(true);
            expect(store.signInStep).toBe(SignInStep.DONE);
        });

        it("propagates verify errors (invalid code) with the secret intact", async () => {
            store.totpSetup = {
                sharedSecret: "ABC",
                setupUri: "otpauth://..."
            };
            auth.verifyTotpSetup.mockRejectedValue(new Error("Invalid code"));

            await expect(store.completeMfaEnrolment("000000")).rejects.toThrow("Invalid code");
            expect(store.totpSetup?.sharedSecret).toBe("ABC");
            expect(auth.getUser).not.toHaveBeenCalled();
        });

        it("on MFA_PREFERENCE_FAILED: resets state, does NOT mark MFA enabled, rethrows", async () => {
            // Same reasoning as confirmTotpSetup: TOTP was verified but
            // the preference didn't stick — letting the page navigate to
            // /projects with mfaEnabled=true would 403 every API call
            // under the app-gate. Page handles the snackbar.
            auth.verifyTotpSetup.mockRejectedValue(new AuthError("MFA_PREFERENCE_FAILED", "Boom"));
            const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});

            await expect(store.completeMfaEnrolment("123456")).rejects.toThrow("Boom");

            expect(consoleSpy).toHaveBeenCalledWith(
                "Failed to set MFA preference post-enrolment:",
                expect.any(Error)
            );
            expect(store.mfaEnabled).toBeNull();
            expect(store.totpSetup).toBeNull();
            expect(auth.getUser).not.toHaveBeenCalled();
            expect(Snackbar.error).not.toHaveBeenCalled();
            consoleSpy.mockRestore();
        });

        it("swallows hydrate failure and leaves mfaEnabled=null", async () => {
            auth.verifyTotpSetup.mockResolvedValue(undefined);
            auth.getUser.mockRejectedValue(new Error("Network"));
            const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});

            await store.completeMfaEnrolment("123456");

            expect(consoleSpy).toHaveBeenCalledWith(
                "Failed to hydrate user post-MFA enrolment:",
                expect.any(Error)
            );
            expect(store.signInStep).toBe(SignInStep.DONE);
            expect(store.mfaEnabled).toBeNull();
            consoleSpy.mockRestore();
        });
    });

    describe("signOut", () => {
        it("asks the provider for a global sign-out where supported, resets store, and tears the page down", async () => {
            store.user = { ...STORE_USER };
            store.signInStep = SignInStep.DONE;

            await store.signOut();

            expect(auth.signOut).toHaveBeenCalledWith({ global: true });
            expect(store.user).toBeNull();
            expect(store.signInStep).toBeNull();
            // A hard navigation, never a router push: a push would leave swrv's
            // module-level cache and every other store in memory for the next
            // account in this tab (FLIP#995).
            expect(leaveToLogin).toHaveBeenCalledTimes(1);
            expect(stashPostSignOutNotice).not.toHaveBeenCalled();
            expect(Snackbar.show).not.toHaveBeenCalled();
            expect(Snackbar.error).not.toHaveBeenCalled();
        });

        it("asks for a local sign-out when the backend has no global one", async () => {
            provider.current = makeMockAuthProvider({
                backend: "keycloak",
                capabilities: NO_CAPABILITIES
            });

            await store.signOut();

            expect((provider.current as ReturnType<typeof makeMockAuthProvider>).signOut)
                .toHaveBeenCalledWith({ global: false });
            expect(leaveToLogin).toHaveBeenCalledTimes(1);
        });

        it("warns the user when the server-side sign-out fails on a real error", async () => {
            // Network/server failure means the refresh token may still be
            // valid server-side; the user needs to be told so they can
            // close the browser to invalidate any cached storage.
            auth.signOut.mockRejectedValue(new Error("network"));
            const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
            store.user = { ...STORE_USER };

            await store.signOut();

            expect(consoleSpy).toHaveBeenCalledWith(
                "Sign out error:",
                expect.any(Error)
            );
            expect(store.user).toBeNull();
            expect(leaveToLogin).toHaveBeenCalledTimes(1);
            // Queued for the login page, not shown into the page being discarded.
            expect(stashPostSignOutNotice).toHaveBeenCalledWith(
                expect.objectContaining({
                    type: "error",
                    title: "Sign-out incomplete"
                })
            );
            expect(Snackbar.error).not.toHaveBeenCalled();
            consoleSpy.mockRestore();
        });

        it("does NOT warn on SESSION_ALREADY_ENDED via the interceptor", async () => {
            // The api.ts 401 interceptor calls signOut({ viaInterceptor: true })
            // with already-invalid tokens; the provider reports the session
            // had already ended. Surfacing any notice here would stack on
            // top of the interceptor's "Not Authorised" message every time
            // the session expires.
            auth.signOut.mockRejectedValue(new AuthError("SESSION_ALREADY_ENDED", "Access Token has been revoked"));
            const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
            store.user = { ...STORE_USER };

            await store.signOut({ viaInterceptor: true });

            expect(store.user).toBeNull();
            expect(leaveToLogin).toHaveBeenCalledTimes(1);
            expect(stashPostSignOutNotice).not.toHaveBeenCalled();
            expect(Snackbar.error).not.toHaveBeenCalled();
            expect(Snackbar.show).not.toHaveBeenCalled();
            consoleSpy.mockRestore();
        });

        it("informs the user on a user-initiated SESSION_ALREADY_ENDED sign-out", async () => {
            // User clicks "Sign out" but the session is already invalid (e.g.
            // an admin force-revoked it via the MFA-reset path). For
            // user-initiated sign-out, show an info-level message.
            auth.signOut.mockRejectedValue(new AuthError("SESSION_ALREADY_ENDED", "Access Token has been revoked"));
            const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
            store.user = { ...STORE_USER };

            await store.signOut();

            expect(store.user).toBeNull();
            expect(leaveToLogin).toHaveBeenCalledTimes(1);
            // Not an "incomplete" error — the session was already gone — but
            // the user should know that's what happened, so an info notice is
            // queued for the login page.
            expect(stashPostSignOutNotice).toHaveBeenCalledWith(
                expect.objectContaining({
                    type: "info",
                    title: "Session already ended"
                })
            );
            expect(Snackbar.error).not.toHaveBeenCalled();
            expect(Snackbar.show).not.toHaveBeenCalled();
            consoleSpy.mockRestore();
        });
    });

    describe("abandonSignIn", () => {
        it("fires the provider sign-out without awaiting it and swallows its failure", async () => {
            let rejectSignOut!: (e: Error) => void;
            auth.signOut.mockImplementationOnce(
                () => new Promise<void>((_resolve, reject) => { rejectSignOut = reject; })
            );

            expect(store.abandonSignIn()).toBeUndefined();
            expect(auth.signOut).toHaveBeenCalledTimes(1);

            rejectSignOut(new Error("nothing to sign out of"));
            // No unhandled rejection surfaces: the catch is the whole point.
            await Promise.resolve();
        });
    });

    describe("resetPassword / updateForgottenPassword", () => {
        it("resetPassword delegates to the provider", async () => {
            await store.resetPassword("u@e.com");

            expect(auth.resetPassword).toHaveBeenCalledWith("u@e.com");
        });

        it("resetPassword propagates a provider failure so the caller can report it", async () => {
            auth.resetPassword.mockRejectedValue(new AuthError("UNSUPPORTED"));

            await expect(store.resetPassword("u@e.com")).rejects.toMatchObject({ code: "UNSUPPORTED" });
        });

        it("updateForgottenPassword delegates to the provider", async () => {
            await store.updateForgottenPassword({
                email: "u@e.com",
                code: "123456",
                newPassword: "new-pw!"  // pragma: allowlist secret
            });

            expect(auth.confirmResetPassword).toHaveBeenCalledWith({
                email: "u@e.com",
                code: "123456",
                newPassword: "new-pw!"  // pragma: allowlist secret
            });
        });
    });

    describe("hasPermissions", () => {
        it("returns true when user has every required permission", () => {
            store.user = {
                ...STORE_USER,
                permissions: ["CanManageUsers", "CanManageProjects"]
            };

            expect(store.hasPermissions(["CanManageUsers"])).toBe(true);
            expect(
                store.hasPermissions(["CanManageUsers", "CanManageProjects"])
            ).toBe(true);
        });

        it("returns false when any required permission is missing", () => {
            store.user = {
                ...STORE_USER,
                permissions: ["CanManageUsers"]
            };

            expect(store.hasPermissions(["CanManageProjects"])).toBe(false);
            expect(
                store.hasPermissions(["CanManageUsers", "CanManageProjects"])
            ).toBe(false);
        });

        it("returns true for an empty permissions list (vacuously satisfied)", () => {
            store.user = { ...STORE_USER };

            expect(store.hasPermissions([])).toBe(true);
        });

        it("returns false when user is null", () => {
            expect(store.hasPermissions(["CanManageUsers"])).toBe(false);
        });
    });
});
