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

import {
    confirmResetPassword,
    confirmSignIn,
    fetchAuthSession,
    fetchUserAttributes,
    getCurrentUser,
    resetPassword,
    setUpTOTP,
    signIn,
    signOut,
    updateMFAPreference,
    verifyTOTPSetup
} from "aws-amplify/auth";
import { createPinia, setActivePinia } from "pinia";

import { routeChange } from "@/router";
import { getMfaStatus, getUserPermissions } from "@/services/user-service";
import { useAuthStore } from "@/store/auth";
import { Snackbar } from "@/utils/snackbar";

// Amplify auth functions are all called via named imports; mock every
// symbol the store touches. Individual tests re-arm these via
// vi.mocked(fn).mockResolvedValue(...) / mockRejectedValue(...).
vi.mock("aws-amplify/auth", () => ({
    confirmResetPassword: vi.fn(),
    confirmSignIn: vi.fn(),
    fetchAuthSession: vi.fn(),
    fetchUserAttributes: vi.fn(),
    getCurrentUser: vi.fn(),
    resetPassword: vi.fn(),
    setUpTOTP: vi.fn(),
    signIn: vi.fn(),
    signOut: vi.fn(),
    updateMFAPreference: vi.fn(),
    verifyTOTPSetup: vi.fn()
}));

vi.mock("@/services/user-service", () => ({
    getMfaStatus: vi.fn(),
    getUserPermissions: vi.fn()
}));

// The store imports @/router only to call routeChange.gotoLogin from
// signOut — stub the module so importing it does not instantiate a real
// router with generated pages.
vi.mock("@/router", () => ({
    default: { push: vi.fn() },
    routeChange: { gotoLogin: vi.fn() }
}));

vi.mock("@/utils/snackbar", () => ({
    Snackbar: {
        show: vi.fn(),
        error: vi.fn(),
        success: vi.fn(),
        warning: vi.fn()
    }
}));

describe("authStore", () => {
    let store: ReturnType<typeof useAuthStore>;

    beforeEach(() => {
        setActivePinia(createPinia());
        store = useAuthStore();
        vi.mocked(signIn).mockReset();
        vi.mocked(confirmSignIn).mockReset();
        vi.mocked(signOut).mockReset();
        vi.mocked(getCurrentUser).mockReset();
        vi.mocked(fetchUserAttributes).mockReset();
        vi.mocked(setUpTOTP).mockReset();
        vi.mocked(verifyTOTPSetup).mockReset();
        vi.mocked(updateMFAPreference).mockReset();
        vi.mocked(resetPassword).mockReset();
        vi.mocked(confirmResetPassword).mockReset();
        vi.mocked(getMfaStatus).mockReset();
        vi.mocked(getUserPermissions).mockReset();
        vi.mocked(routeChange.gotoLogin).mockReset();
        vi.mocked(fetchAuthSession).mockReset();
        vi.mocked(Snackbar.error).mockReset();
        // Default: tokens are visible immediately so waitForSessionTokens
        // resolves without triggering the forceRefresh fallback. Tests that
        // exercise the race re-arm this with mockResolvedValueOnce.
        vi.mocked(fetchAuthSession).mockResolvedValue({
            tokens: { accessToken: { toString: () => "access-token" } as never }
        } as never);
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
            store.user = {
                username: "u",
                userId: "id",
                attributes: { sub: "s", email: "e@f.com" },
                permissions: []
            };
            expect(store.getUser).toEqual(store.user);
        });

        it("confirmedUser is true once challenges clear AND (env is MFA-off OR TOTP is active)", () => {
            expect(store.confirmedUser).toBe(false);

            store.signInStep = "DONE";
            store.user = {
                username: "u",
                userId: "id",
                attributes: { sub: "s", email: "e@f.com" },
                permissions: []
            };
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

            store.signInStep = "DONE";
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
            vi.mocked(getCurrentUser).mockResolvedValue({
                username: "u",
                userId: "id"
            } as never);
            vi.mocked(getMfaStatus).mockResolvedValue({ enabled: true, required: true });
            vi.mocked(fetchUserAttributes).mockResolvedValue({
                sub: "s",
                email: "e@f.com"
            } as never);
            vi.mocked(getUserPermissions).mockResolvedValue({
                permissions: ["CanManageUsers"]
            });

            await store.hydrate();

            expect(store.signInStep).toBe("DONE");
            expect(store.mfaEnabled).toBe(true);
            expect(store.user).toEqual({
                username: "u",
                userId: "id",
                attributes: { sub: "s", email: "e@f.com" },
                permissions: ["CanManageUsers"]
            });
            expect(getUserPermissions).toHaveBeenCalledWith("id");
            expect(getMfaStatus).toHaveBeenCalledTimes(1);
        });

        it("defaults permissions to empty array when backend omits them", async () => {
            vi.mocked(getCurrentUser).mockResolvedValue({
                username: "u",
                userId: "id"
            } as never);
            vi.mocked(getMfaStatus).mockResolvedValue({ enabled: true, required: true });
            vi.mocked(fetchUserAttributes).mockResolvedValue({
                sub: "s",
                email: "e@f.com"
            } as never);
            // getUserPermissions returns an object with no permissions key
            vi.mocked(getUserPermissions).mockResolvedValue({} as never);

            await store.hydrate();

            expect(store.user?.permissions).toEqual([]);
        });

        it("with mfa disabled, skips permissions fetch and leaves permissions empty", async () => {
            vi.mocked(getCurrentUser).mockResolvedValue({
                username: "u",
                userId: "id"
            } as never);
            vi.mocked(getMfaStatus).mockResolvedValue({ enabled: false, required: true });
            vi.mocked(fetchUserAttributes).mockResolvedValue({
                sub: "s",
                email: "e@f.com"
            } as never);

            await store.hydrate();

            expect(store.signInStep).toBe("DONE");
            expect(store.mfaEnabled).toBe(false);
            expect(store.user).toEqual({
                username: "u",
                userId: "id",
                attributes: { sub: "s", email: "e@f.com" },
                permissions: []
            });
            expect(getUserPermissions).not.toHaveBeenCalled();
        });

        it("accepts a knownMfaEnabled override and skips the backend call", async () => {
            vi.mocked(getCurrentUser).mockResolvedValue({
                username: "u",
                userId: "id"
            } as never);
            vi.mocked(fetchUserAttributes).mockResolvedValue({
                sub: "s",
                email: "e@f.com"
            } as never);
            vi.mocked(getUserPermissions).mockResolvedValue({ permissions: [] });

            await store.hydrate({ enabled: true, required: true });

            expect(getMfaStatus).not.toHaveBeenCalled();
            expect(store.mfaEnabled).toBe(true);
            expect(store.mfaRequired).toBe(true);
        });

        it("still fetches permissions when MFA is not enabled but this environment doesn't require it", async () => {
            // Dev bypass: a user who never enrolled TOTP in an environment
            // with ENFORCE_MFA=false still has full API access, so the
            // store should populate real permissions instead of the
            // attributes-only placeholder reserved for the MFA-blocked case.
            vi.mocked(getCurrentUser).mockResolvedValue({
                username: "u",
                userId: "id"
            } as never);
            vi.mocked(getMfaStatus).mockResolvedValue({ enabled: false, required: false });
            vi.mocked(fetchUserAttributes).mockResolvedValue({
                sub: "s",
                email: "e@f.com"
            } as never);
            vi.mocked(getUserPermissions).mockResolvedValue({
                permissions: ["CanManageUsers"]
            });

            await store.hydrate();

            expect(store.mfaEnabled).toBe(false);
            expect(store.mfaRequired).toBe(false);
            expect(store.user?.permissions).toEqual(["CanManageUsers"]);
            expect(getUserPermissions).toHaveBeenCalledWith("id");
        });
    });

    describe("fetchInfo", () => {
        it("delegates to hydrate", async () => {
            vi.mocked(getCurrentUser).mockResolvedValue({
                username: "u",
                userId: "id"
            } as never);
            vi.mocked(getMfaStatus).mockResolvedValue({ enabled: false, required: true });
            vi.mocked(fetchUserAttributes).mockResolvedValue({
                sub: "s",
                email: "e@f.com"
            } as never);

            await store.fetchInfo();

            expect(store.signInStep).toBe("DONE");
            expect(store.mfaEnabled).toBe(false);
        });
    });

    describe("finaliseSignIn", () => {
        it("delegates to hydrate", async () => {
            vi.mocked(getCurrentUser).mockResolvedValue({
                username: "u",
                userId: "id"
            } as never);
            vi.mocked(getMfaStatus).mockResolvedValue({ enabled: true, required: true });
            vi.mocked(fetchUserAttributes).mockResolvedValue({
                sub: "s",
                email: "e@f.com"
            } as never);
            vi.mocked(getUserPermissions).mockResolvedValue({ permissions: [] });

            await store.finaliseSignIn();

            expect(store.signInStep).toBe("DONE");
            expect(store.mfaEnabled).toBe(true);
        });
    });

    describe("signIn", () => {
        it("resets state then hydrates when Amplify reports isSignedIn", async () => {
            // Pre-set state that must be cleared before sign-in runs.
            store.user = {
                username: "stale",
                userId: "stale",
                attributes: { sub: "", email: "" },
                permissions: []
            };
            store.signInStep = "DONE";
            store.mfaEnabled = true;
            vi.mocked(signIn).mockResolvedValue({
                isSignedIn: true,
                nextStep: { signInStep: "DONE" }
            } as never);
            vi.mocked(getCurrentUser).mockResolvedValue({
                username: "u",
                userId: "id"
            } as never);
            vi.mocked(getMfaStatus).mockResolvedValue({ enabled: true, required: true });
            vi.mocked(fetchUserAttributes).mockResolvedValue({
                sub: "s",
                email: "e@f.com"
            } as never);
            vi.mocked(getUserPermissions).mockResolvedValue({ permissions: [] });

            await store.signIn({ username: "u", password: "p" });

            expect(signIn).toHaveBeenCalledWith({
                username: "u",
                password: "p",
                options: { authFlowType: "USER_PASSWORD_AUTH" }
            });
            expect(store.signInStep).toBe("DONE");
            expect(store.mfaEnabled).toBe(true);
            expect(store.user?.username).toBe("u");
        });

        it("captures TOTP setup details when Cognito demands first-time enrolment", async () => {
            const setupUri = new URL("otpauth://totp/FLIP:u?secret=ABCD");
            vi.mocked(signIn).mockResolvedValue({
                isSignedIn: false,
                nextStep: {
                    signInStep: "CONTINUE_SIGN_IN_WITH_TOTP_SETUP",
                    totpSetupDetails: {
                        sharedSecret: "ABCD",
                        getSetupUri: vi.fn(() => setupUri)
                    }
                }
            } as never);

            await store.signIn({ username: "u", password: "p" });

            expect(store.signInStep).toBe("CONTINUE_SIGN_IN_WITH_TOTP_SETUP");
            expect(store.totpSetup).toEqual({
                sharedSecret: "ABCD",
                setupUri: setupUri.toString()
            });
            // Did NOT hydrate — we're mid-challenge.
            expect(getCurrentUser).not.toHaveBeenCalled();
        });

        it("returns early for new-password challenge without hydrating", async () => {
            vi.mocked(signIn).mockResolvedValue({
                isSignedIn: false,
                nextStep: {
                    signInStep: "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED"
                }
            } as never);

            await store.signIn({ username: "u", password: "p" });

            expect(store.signInStep).toBe("CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED");
            expect(getCurrentUser).not.toHaveBeenCalled();
        });

        it("returns early for TOTP-code challenge without hydrating", async () => {
            vi.mocked(signIn).mockResolvedValue({
                isSignedIn: false,
                nextStep: { signInStep: "CONFIRM_SIGN_IN_WITH_TOTP_CODE" }
            } as never);

            await store.signIn({ username: "u", password: "p" });

            expect(store.signInStep).toBe("CONFIRM_SIGN_IN_WITH_TOTP_CODE");
            expect(getCurrentUser).not.toHaveBeenCalled();
        });

        it("tolerates a response with no nextStep", async () => {
            vi.mocked(signIn).mockResolvedValue({
                isSignedIn: false,
                nextStep: undefined
            } as never);

            await store.signIn({ username: "u", password: "p" });

            expect(store.signInStep).toBeNull();
            expect(getCurrentUser).not.toHaveBeenCalled();
        });

        it("throws MissingSessionTokensError when forceRefresh fails and no accessToken is available", async () => {
            // Amplify's forceRefresh can throw on its own (expired refresh
            // token, Cognito transient failure). The wait helper logs the
            // throw and the "no accessToken" warn, then throws a typed error
            // so the caller can surface a real message instead of letting
            // hydrate proceed unauthenticated and 401 generically.
            const consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
            const consoleWarnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
            vi.mocked(fetchAuthSession)
                .mockReset()
                .mockResolvedValueOnce({ tokens: undefined } as never)
                .mockRejectedValueOnce(new Error("Refresh token expired"));
            vi.mocked(signIn).mockResolvedValue({
                isSignedIn: true,
                nextStep: { signInStep: "DONE" }
            } as never);

            await expect(store.signIn({ username: "u", password: "p" })).rejects.toMatchObject({
                name: "MissingSessionTokensError"
            });

            // hydrate must not have been attempted — `getCurrentUser` is
            // hydrate's first Amplify call and would proceed if the wait
            // helper had silently returned.
            expect(getCurrentUser).not.toHaveBeenCalled();

            expect(consoleErrorSpy).toHaveBeenCalledWith(
                "waitForSessionTokens: forceRefresh threw:",
                expect.objectContaining({ message: "Refresh token expired" })
            );
            // Also covers the "still no accessToken after forceRefresh" warn —
            // the rejected forceRefresh leaves `session.tokens` undefined.
            expect(consoleWarnSpy).toHaveBeenCalledWith(
                "waitForSessionTokens: no accessToken after forceRefresh",
                expect.any(Object)
            );
            consoleErrorSpy.mockRestore();
            consoleWarnSpy.mockRestore();
        });

        it("forces a session refresh when tokens are not yet visible after signIn", async () => {
            // Amplify v6 can resolve signIn before fetchAuthSession sees the
            // cached tokens; this race used to surface as a 401 on the very
            // first post-signIn backend call. Arm fetchAuthSession to return
            // empty on the first read, then populated on the forceRefresh
            // retry, and confirm hydrate still runs to completion.
            vi.mocked(fetchAuthSession)
                .mockReset()
                .mockResolvedValueOnce({ tokens: undefined } as never)
                .mockResolvedValueOnce({
                    tokens: { accessToken: { toString: () => "access" } as never }
                } as never);
            vi.mocked(signIn).mockResolvedValue({
                isSignedIn: true,
                nextStep: { signInStep: "DONE" }
            } as never);
            vi.mocked(getCurrentUser).mockResolvedValue({
                username: "u",
                userId: "id"
            } as never);
            vi.mocked(getMfaStatus).mockResolvedValue({ enabled: true, required: true });
            vi.mocked(fetchUserAttributes).mockResolvedValue({
                sub: "s",
                email: "e@f.com"
            } as never);
            vi.mocked(getUserPermissions).mockResolvedValue({ permissions: [] });

            await store.signIn({ username: "u", password: "p" });

            expect(fetchAuthSession).toHaveBeenCalledTimes(2);
            expect(fetchAuthSession).toHaveBeenNthCalledWith(2, { forceRefresh: true });
            expect(store.user?.username).toBe("u");
        });

        it("signs the stale session out and retries on UserAlreadyAuthenticatedException", async () => {
            // Amplify v6 throws this when local storage already holds Cognito
            // tokens — e.g. the user typed credentials in /login while another
            // tab still has a live session. Recover by signing the stale
            // session out and retrying once, otherwise the user is stuck on
            // the login page until they clear storage by hand.
            const stale = Object.assign(new Error("There is already a signed in user."), {
                name: "UserAlreadyAuthenticatedException"
            });
            vi.mocked(signIn)
                .mockReset()
                .mockRejectedValueOnce(stale)
                .mockResolvedValueOnce({
                    isSignedIn: true,
                    nextStep: { signInStep: "DONE" }
                } as never);
            vi.mocked(signOut).mockResolvedValue(undefined as never);
            vi.mocked(getCurrentUser).mockResolvedValue({
                username: "u",
                userId: "id"
            } as never);
            vi.mocked(getMfaStatus).mockResolvedValue({ enabled: true, required: true });
            vi.mocked(fetchUserAttributes).mockResolvedValue({
                sub: "s",
                email: "e@f.com"
            } as never);
            vi.mocked(getUserPermissions).mockResolvedValue({ permissions: [] });

            await store.signIn({ username: "u", password: "p" });

            expect(signIn).toHaveBeenCalledTimes(2);
            expect(signOut).toHaveBeenCalledTimes(1);
            // signOut must run between the two signIn attempts; otherwise the
            // retry hits the same exception in a loop.
            const signOutOrder = vi.mocked(signOut).mock.invocationCallOrder[0];
            const signInOrders = vi.mocked(signIn).mock.invocationCallOrder;
            expect(signOutOrder).toBeGreaterThan(signInOrders[0]);
            expect(signOutOrder).toBeLessThan(signInOrders[1]);
            expect(store.user?.username).toBe("u");
        });

        it("propagates UserAlreadyAuthenticatedException from the retry without a second retry", async () => {
            // Single-retry contract: if the post-signOut signIn ALSO throws
            // UserAlreadyAuthenticatedException (token write hadn't settled,
            // another tab racing), the store must let the error bubble
            // instead of retrying again. Two attempts max, no infinite loop.
            const stale = Object.assign(new Error("There is already a signed in user."), {
                name: "UserAlreadyAuthenticatedException"
            });
            vi.mocked(signIn)
                .mockReset()
                .mockRejectedValueOnce(stale)
                .mockRejectedValueOnce(stale);
            vi.mocked(signOut).mockResolvedValue(undefined as never);

            await expect(
                store.signIn({ username: "u", password: "p" })
            ).rejects.toMatchObject({
                name: "UserAlreadyAuthenticatedException"
            });

            expect(signIn).toHaveBeenCalledTimes(2);
            expect(signOut).toHaveBeenCalledTimes(1);
            // hydrate must not have been attempted — Cognito never accepted
            // the credentials, so getCurrentUser would 401 generically.
            expect(getCurrentUser).not.toHaveBeenCalled();
        });

        it("does not retry on errors other than UserAlreadyAuthenticatedException", async () => {
            const wrongPassword = Object.assign(new Error("Incorrect username or password."), {
                name: "NotAuthorizedException"
            });
            vi.mocked(signIn).mockReset().mockRejectedValue(wrongPassword);

            await expect(
                store.signIn({ username: "u", password: "p" })
            ).rejects.toThrow("Incorrect username or password.");

            expect(signIn).toHaveBeenCalledTimes(1);
            expect(signOut).not.toHaveBeenCalled();
        });

        it("rethrows post-signIn hydrate failures so Login.vue surfaces them", async () => {
            // Cognito accepted the creds (isSignedIn=true) but the follow-up
            // backend call failed. The store must log the underlying error and
            // rethrow so the login page can tell the user something went wrong
            // — silently resolving here lets a broken session masquerade as
            // success and the next route-guarded call 401s.
            vi.mocked(signIn).mockResolvedValue({
                isSignedIn: true,
                nextStep: { signInStep: "DONE" }
            } as never);
            vi.mocked(getCurrentUser).mockResolvedValue({
                username: "u",
                userId: "id"
            } as never);
            vi.mocked(getMfaStatus).mockRejectedValue(
                new Error("Request failed with status code 401")
            );
            const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});

            await expect(
                store.signIn({ username: "u", password: "p" })
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
            vi.mocked(confirmSignIn).mockResolvedValue({
                isSignedIn: true,
                nextStep: { signInStep: "DONE" }
            } as never);
            vi.mocked(getCurrentUser).mockResolvedValue({
                username: "u",
                userId: "id"
            } as never);
            vi.mocked(getMfaStatus).mockResolvedValue({ enabled: false, required: true });
            vi.mocked(fetchUserAttributes).mockResolvedValue({
                sub: "s",
                email: "e@f.com"
            } as never);

            await store.changePassword("newPassword123!");

            expect(confirmSignIn).toHaveBeenCalledWith({
                challengeResponse: "newPassword123!"
            });
            expect(store.signInStep).toBe("DONE");
            expect(store.mfaEnabled).toBe(false);
        });

        it("captures TOTP setup details when next step is MFA setup", async () => {
            const setupUri = new URL("otpauth://totp/FLIP:u?secret=XYZ");
            vi.mocked(confirmSignIn).mockResolvedValue({
                isSignedIn: false,
                nextStep: {
                    signInStep: "CONTINUE_SIGN_IN_WITH_TOTP_SETUP",
                    totpSetupDetails: {
                        sharedSecret: "XYZ",
                        getSetupUri: vi.fn(() => setupUri)
                    }
                }
            } as never);

            await store.changePassword("newPassword123!");

            expect(store.signInStep).toBe("CONTINUE_SIGN_IN_WITH_TOTP_SETUP");
            expect(store.totpSetup).toEqual({
                sharedSecret: "XYZ",
                setupUri: setupUri.toString()
            });
            expect(getCurrentUser).not.toHaveBeenCalled();
        });

        it("does nothing special on unknown step + not signed in", async () => {
            vi.mocked(confirmSignIn).mockResolvedValue({
                isSignedIn: false,
                nextStep: { signInStep: "DONE" }
            } as never);

            await store.changePassword("newPassword123!");

            expect(store.signInStep).toBe("DONE");
            expect(getCurrentUser).not.toHaveBeenCalled();
        });
    });

    describe("confirmTotpChallenge", () => {
        it("returns early when Cognito reports not signed in", async () => {
            vi.mocked(confirmSignIn).mockResolvedValue({
                isSignedIn: false,
                nextStep: undefined
            } as never);

            await store.confirmTotpChallenge("123456");

            expect(store.signInStep).toBeNull();
            expect(getCurrentUser).not.toHaveBeenCalled();
        });

        it("hydrates with known-true MFA when challenge clears", async () => {
            vi.mocked(confirmSignIn).mockResolvedValue({
                isSignedIn: true,
                nextStep: undefined
            } as never);
            vi.mocked(getCurrentUser).mockResolvedValue({
                username: "u",
                userId: "id"
            } as never);
            vi.mocked(fetchUserAttributes).mockResolvedValue({
                sub: "s",
                email: "e@f.com"
            } as never);
            vi.mocked(getUserPermissions).mockResolvedValue({ permissions: [] });

            await store.confirmTotpChallenge("123456");

            // Did NOT call getMfaStatus — hydrate(true) short-circuits it.
            expect(getMfaStatus).not.toHaveBeenCalled();
            expect(store.signInStep).toBe("DONE");
            expect(store.mfaEnabled).toBe(true);
        });

        it("swallows post-success hydrate failures and leaves mfaEnabled=null", async () => {
            vi.mocked(confirmSignIn).mockResolvedValue({
                isSignedIn: true,
                nextStep: undefined
            } as never);
            vi.mocked(getCurrentUser).mockRejectedValue(new Error("Network Error"));
            const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});

            await expect(store.confirmTotpChallenge("123456")).resolves.toBeUndefined();

            expect(store.signInStep).toBe("DONE");
            expect(store.mfaEnabled).toBeNull();
            expect(consoleSpy).toHaveBeenCalled();
            consoleSpy.mockRestore();
        });
    });

    describe("confirmTotpSetup", () => {
        it("returns early when Cognito reports not signed in", async () => {
            vi.mocked(confirmSignIn).mockResolvedValue({
                isSignedIn: false,
                nextStep: undefined
            } as never);

            await store.confirmTotpSetup("123456");

            expect(updateMFAPreference).not.toHaveBeenCalled();
            expect(store.totpSetup).toBeNull();
        });

        it("sets MFA preference, clears totpSetup, then hydrates with known-true MFA", async () => {
            store.totpSetup = { sharedSecret: "ABC", setupUri: "otpauth://..." };
            vi.mocked(confirmSignIn).mockResolvedValue({
                isSignedIn: true,
                nextStep: undefined
            } as never);
            vi.mocked(updateMFAPreference).mockResolvedValue(undefined as never);
            vi.mocked(getCurrentUser).mockResolvedValue({
                username: "u",
                userId: "id"
            } as never);
            vi.mocked(fetchUserAttributes).mockResolvedValue({
                sub: "s",
                email: "e@f.com"
            } as never);
            vi.mocked(getUserPermissions).mockResolvedValue({ permissions: [] });

            await store.confirmTotpSetup("123456");

            expect(updateMFAPreference).toHaveBeenCalledWith({ totp: "PREFERRED" });
            expect(store.totpSetup).toBeNull();
            expect(store.mfaEnabled).toBe(true);
            expect(store.signInStep).toBe("DONE");
            expect(getMfaStatus).not.toHaveBeenCalled();
        });

        it("rethrows updateMFAPreference failure and does NOT mark MFA enabled", async () => {
            // Cognito accepted the verification code but the preference
            // didn't stick. Painting `mfaEnabled=true` here would let the
            // user into the app where every API call 403s under the
            // app-gate. The store rethrows so the calling page (mfa-setup
            // or mfa-verify) keeps the user on the form; the page is
            // responsible for the user-facing snackbar so we don't
            // double-notify.
            vi.mocked(confirmSignIn).mockResolvedValue({
                isSignedIn: true,
                nextStep: undefined
            } as never);
            vi.mocked(updateMFAPreference).mockRejectedValue(
                new Error("Cognito boom")
            );
            const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});

            await expect(store.confirmTotpSetup("123456")).rejects.toThrow("Cognito boom");

            expect(consoleSpy).toHaveBeenCalledWith(
                "Failed to set MFA preference post-setup:",
                expect.any(Error)
            );
            expect(store.mfaEnabled).toBeNull();
            expect(store.totpSetup).toBeNull();
            // hydrate must not run after a fatal preference failure —
            // forcing a fresh authoritative read happens on the next
            // navigation via the router guard.
            expect(getCurrentUser).not.toHaveBeenCalled();
            expect(getMfaStatus).not.toHaveBeenCalled();
            // Snackbar is the page's job, not the store's.
            expect(Snackbar.error).not.toHaveBeenCalled();
            consoleSpy.mockRestore();
        });

        it("swallows hydrate failure and leaves mfaEnabled=null for router guard", async () => {
            vi.mocked(confirmSignIn).mockResolvedValue({
                isSignedIn: true,
                nextStep: undefined
            } as never);
            vi.mocked(updateMFAPreference).mockResolvedValue(undefined as never);
            vi.mocked(getCurrentUser).mockRejectedValue(new Error("Network"));
            const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});

            await expect(store.confirmTotpSetup("123456")).resolves.toBeUndefined();

            expect(consoleSpy).toHaveBeenCalledWith(
                "Failed to hydrate user post-MFA setup:",
                expect.any(Error)
            );
            expect(store.signInStep).toBe("DONE");
            expect(store.mfaEnabled).toBeNull();
            consoleSpy.mockRestore();
        });
    });

    describe("captureTotpSetupDetails", () => {
        it("uses pendingUsername as the account label, not the sharedSecret", () => {
            const url = new URL("otpauth://totp/FLIP:u?secret=SEC");
            const getSetupUri = vi.fn(() => url);
            store.pendingUsername = "u@e.com";
            store.captureTotpSetupDetails({
                totpSetupDetails: { sharedSecret: "SEC", getSetupUri }
            });

            expect(getSetupUri).toHaveBeenCalledWith("FLIP", "u@e.com");
            expect(store.totpSetup).toEqual({
                sharedSecret: "SEC",
                setupUri: url.toString()
            });
        });

        it("omits the account label when pendingUsername is null", () => {
            const url = new URL("otpauth://totp/FLIP?secret=SEC");
            const getSetupUri = vi.fn(() => url);
            store.pendingUsername = null;
            store.captureTotpSetupDetails({
                totpSetupDetails: { sharedSecret: "SEC", getSetupUri }
            });

            expect(getSetupUri).toHaveBeenCalledWith("FLIP", undefined);
        });

        it("is a no-op when totpSetupDetails are missing", () => {
            store.captureTotpSetupDetails({});
            expect(store.totpSetup).toBeNull();

            store.captureTotpSetupDetails({ totpSetupDetails: undefined });
            expect(store.totpSetup).toBeNull();
        });
    });

    describe("beginMfaEnrolment", () => {
        it("records shared secret and setup URI from Amplify", async () => {
            const url = new URL("otpauth://totp/FLIP:u?secret=SEC");
            const getSetupUri = vi.fn(() => url);
            vi.mocked(setUpTOTP).mockResolvedValue({
                sharedSecret: "SEC",
                getSetupUri
            } as never);
            store.user = {
                username: "u",
                userId: "id",
                attributes: { sub: "s", email: "e@f.com" },
                permissions: []
            };

            await store.beginMfaEnrolment();

            expect(getSetupUri).toHaveBeenCalledWith("FLIP", "e@f.com");
            expect(store.totpSetup).toEqual({
                sharedSecret: "SEC",
                setupUri: url.toString()
            });
        });

        it("falls back to empty string when Amplify omits sharedSecret", async () => {
            const url = new URL("otpauth://totp/FLIP:u");
            vi.mocked(setUpTOTP).mockResolvedValue({
                sharedSecret: undefined,
                getSetupUri: vi.fn(() => url)
            } as never);
            // No user in the store — appName arg should still be "FLIP" and
            // email undefined (we just want the call not to crash).
            await store.beginMfaEnrolment();

            expect(store.totpSetup?.sharedSecret).toBe("");
        });
    });

    describe("completeMfaEnrolment", () => {
        it("verifies code, sets MFA preference, clears setup, and hydrates known-true", async () => {
            store.totpSetup = { sharedSecret: "ABC", setupUri: "otpauth://..." };
            vi.mocked(verifyTOTPSetup).mockResolvedValue(undefined as never);
            vi.mocked(updateMFAPreference).mockResolvedValue(undefined as never);
            vi.mocked(getCurrentUser).mockResolvedValue({
                username: "u",
                userId: "id"
            } as never);
            vi.mocked(fetchUserAttributes).mockResolvedValue({
                sub: "s",
                email: "e@f.com"
            } as never);
            vi.mocked(getUserPermissions).mockResolvedValue({ permissions: [] });

            await store.completeMfaEnrolment("123456");

            expect(verifyTOTPSetup).toHaveBeenCalledWith({ code: "123456" });
            expect(updateMFAPreference).toHaveBeenCalledWith({ totp: "PREFERRED" });
            expect(store.totpSetup).toBeNull();
            expect(store.mfaEnabled).toBe(true);
            expect(store.signInStep).toBe("DONE");
        });

        it("propagates verifyTOTPSetup errors (invalid code)", async () => {
            vi.mocked(verifyTOTPSetup).mockRejectedValue(new Error("Invalid code"));

            await expect(store.completeMfaEnrolment("000000")).rejects.toThrow(
                "Invalid code"
            );
            expect(updateMFAPreference).not.toHaveBeenCalled();
        });

        it("rethrows updateMFAPreference failure and does NOT mark MFA enabled", async () => {
            // Same reasoning as confirmTotpSetup: TOTP was verified but
            // the preference didn't stick — letting the page navigate to
            // /projects with mfaEnabled=true would 403 every API call
            // under the app-gate. Page handles the snackbar.
            vi.mocked(verifyTOTPSetup).mockResolvedValue(undefined as never);
            vi.mocked(updateMFAPreference).mockRejectedValue(new Error("Boom"));
            const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});

            await expect(store.completeMfaEnrolment("123456")).rejects.toThrow("Boom");

            expect(consoleSpy).toHaveBeenCalledWith(
                "Failed to set MFA preference post-enrolment:",
                expect.any(Error)
            );
            expect(store.mfaEnabled).toBeNull();
            expect(store.totpSetup).toBeNull();
            expect(getCurrentUser).not.toHaveBeenCalled();
            expect(Snackbar.error).not.toHaveBeenCalled();
            consoleSpy.mockRestore();
        });

        it("swallows hydrate failure and leaves mfaEnabled=null", async () => {
            vi.mocked(verifyTOTPSetup).mockResolvedValue(undefined as never);
            vi.mocked(updateMFAPreference).mockResolvedValue(undefined as never);
            vi.mocked(getCurrentUser).mockRejectedValue(new Error("Network"));
            const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});

            await store.completeMfaEnrolment("123456");

            expect(consoleSpy).toHaveBeenCalledWith(
                "Failed to hydrate user post-MFA enrolment:",
                expect.any(Error)
            );
            expect(store.signInStep).toBe("DONE");
            expect(store.mfaEnabled).toBeNull();
            consoleSpy.mockRestore();
        });
    });

    describe("signOut", () => {
        it("calls Amplify global sign-out, resets store, and routes to login", async () => {
            vi.mocked(signOut).mockResolvedValue(undefined as never);
            store.user = {
                username: "u",
                userId: "id",
                attributes: { sub: "s", email: "e@f.com" },
                permissions: []
            };
            store.signInStep = "DONE";

            await store.signOut();

            expect(signOut).toHaveBeenCalledWith({ global: true });
            expect(store.user).toBeNull();
            expect(store.signInStep).toBeNull();
            expect(routeChange.gotoLogin).toHaveBeenCalledTimes(1);
        });

        it("warns the user when GlobalSignOut fails on a real error", async () => {
            // Network/server failure means the refresh token may still be
            // valid in Cognito; the user needs to be told so they can
            // close the browser to invalidate any cached storage.
            vi.mocked(signOut).mockRejectedValue(new Error("network"));
            const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
            store.user = {
                username: "u",
                userId: "id",
                attributes: { sub: "s", email: "e@f.com" },
                permissions: []
            };

            await store.signOut();

            expect(consoleSpy).toHaveBeenCalledWith(
                "Sign out error:",
                expect.any(Error)
            );
            expect(store.user).toBeNull();
            expect(routeChange.gotoLogin).toHaveBeenCalledTimes(1);
            expect(Snackbar.error).toHaveBeenCalledWith(expect.objectContaining({
                title: "Sign-out incomplete"
            }));
            consoleSpy.mockRestore();
        });

        it("does NOT warn when GlobalSignOut throws NotAuthorizedException", async () => {
            // The api.ts 401 interceptor calls signOut() with already-
            // invalid tokens; Cognito rejects GlobalSignOut with
            // NotAuthorizedException as expected. Surfacing "Sign-out
            // incomplete" here would stack a second snackbar on top of
            // the interceptor's "Not Authorised" message every time the
            // session expires.
            const expected = Object.assign(new Error("Access Token has been revoked"), {
                name: "NotAuthorizedException"
            });
            vi.mocked(signOut).mockRejectedValue(expected);
            const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
            store.user = {
                username: "u",
                userId: "id",
                attributes: { sub: "s", email: "e@f.com" },
                permissions: []
            };

            await store.signOut();

            expect(store.user).toBeNull();
            expect(routeChange.gotoLogin).toHaveBeenCalledTimes(1);
            expect(Snackbar.error).not.toHaveBeenCalled();
            consoleSpy.mockRestore();
        });
    });

    describe("resetPassword", () => {
        it("proxies to Amplify resetPassword with web-app metadata", async () => {
            const amplifyResponse = { isPasswordReset: false } as never;
            vi.mocked(resetPassword).mockResolvedValue(amplifyResponse);

            const result = await store.resetPassword("u@e.com");

            expect(resetPassword).toHaveBeenCalledWith({
                username: "u@e.com",
                options: { clientMetadata: { source: "web-app" } }
            });
            expect(result).toBe(amplifyResponse);
        });
    });

    describe("updateForgottenPassword", () => {
        it("proxies to Amplify confirmResetPassword with web-app metadata", async () => {
            const amplifyResponse = undefined as never;
            vi.mocked(confirmResetPassword).mockResolvedValue(amplifyResponse);

            const result = await store.updateForgottenPassword({
                email: "u@e.com",
                code: "123456",
                newPassword: "new-pw!"
            });

            expect(confirmResetPassword).toHaveBeenCalledWith({
                username: "u@e.com",
                confirmationCode: "123456",
                newPassword: "new-pw!",
                options: { clientMetadata: { source: "web-app" } }
            });
            expect(result).toBe(amplifyResponse);
        });
    });

    describe("hasPermissions", () => {
        it("returns true when user has every required permission", () => {
            store.user = {
                username: "u",
                userId: "id",
                attributes: { sub: "s", email: "e@f.com" },
                permissions: ["CanManageUsers", "CanManageProjects"]
            };

            expect(store.hasPermissions(["CanManageUsers"])).toBe(true);
            expect(
                store.hasPermissions(["CanManageUsers", "CanManageProjects"])
            ).toBe(true);
        });

        it("returns false when any required permission is missing", () => {
            store.user = {
                username: "u",
                userId: "id",
                attributes: { sub: "s", email: "e@f.com" },
                permissions: ["CanManageUsers"]
            };

            expect(store.hasPermissions(["CanManageProjects"])).toBe(false);
            expect(
                store.hasPermissions(["CanManageUsers", "CanManageProjects"])
            ).toBe(false);
        });

        it("returns true for an empty permissions list (vacuously satisfied)", () => {
            store.user = {
                username: "u",
                userId: "id",
                attributes: { sub: "s", email: "e@f.com" },
                permissions: []
            };

            expect(store.hasPermissions([])).toBe(true);
        });

        it("returns false when user is null", () => {
            expect(store.hasPermissions(["CanManageUsers"])).toBe(false);
        });
    });
});
