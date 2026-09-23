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

import { Amplify } from "aws-amplify";
import { confirmResetPassword,
    confirmSignIn,
    fetchAuthSession,
    fetchUserAttributes,
    getCurrentUser,
    resetPassword,
    setUpTOTP,
    signIn,
    signOut,
    updateMFAPreference,
    verifyTOTPSetup } from "aws-amplify/auth";
import { Hub } from "aws-amplify/utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CognitoAuthProvider } from "@/auth/cognito-provider";
import { AuthError, SignInStep } from "@/auth/provider";

// Amplify auth functions are all called via named imports; mock every symbol
// the provider touches. Individual tests re-arm these via
// vi.mocked(fn).mockResolvedValue(...) / mockRejectedValue(...).
vi.mock("aws-amplify", () => ({ Amplify: { configure: vi.fn() } }));
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
vi.mock("aws-amplify/utils", () => ({
    Hub: {
        listen: vi.fn(),
        dispatch: vi.fn()
    }
}));

// env.d.ts declares the window.AWS_* keys as required strings; the specs
// need them absent, hence the untyped delete.
const win = window as unknown as Record<string, unknown>;

const session = (token: string | undefined) =>
    ({ tokens: token ? { accessToken: { toString: () => token } } : undefined }) as never;

describe("CognitoAuthProvider", () => {
    let provider: CognitoAuthProvider;

    beforeEach(() => {
        provider = new CognitoAuthProvider();
        vi.mocked(Amplify.configure).mockReset();
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
        vi.mocked(fetchAuthSession).mockReset();
        vi.mocked(Hub.listen).mockReset();
        // Default: tokens are visible immediately so waitForSessionTokens
        // resolves without triggering the forceRefresh fallback. Tests that
        // exercise the race re-arm this with mockResolvedValueOnce.
        vi.mocked(fetchAuthSession).mockResolvedValue(session("access-token"));
    });

    afterEach(() => {
        delete win.AWS_REGION;
        delete win.AWS_USER_POOL_ID;
        delete win.AWS_CLIENT_ID;
    });

    describe("identity and capabilities", () => {
        it("identifies as the cognito backend with every capability", () => {
            expect(provider.backend).toBe("cognito");
            expect(provider.capabilities).toEqual({
                newPasswordChallenge: true,
                totpChallenge: true,
                totpEnrolment: true,
                forgotPassword: true,
                adminResetPassword: true,
                globalSignOut: true
            });
        });
    });

    describe("configure", () => {
        it("configures Amplify from the window.AWS_* runtime values", () => {
            win.AWS_REGION = "eu-west-1";
            win.AWS_USER_POOL_ID = "eu-west-1_POOL";
            win.AWS_CLIENT_ID = "client-1";

            provider.configure();

            expect(Amplify.configure).toHaveBeenCalledWith({
                Auth: {
                    Cognito: {
                        region: "eu-west-1",
                        userPoolId: "eu-west-1_POOL",
                        userPoolClientId: "client-1"
                    }
                }
            });
        });

        it("defaults the region to eu-west-2 when unset", () => {
            win.AWS_USER_POOL_ID = "p";
            win.AWS_CLIENT_ID = "c";

            provider.configure();

            expect(vi.mocked(Amplify.configure).mock.calls[0][0]).toMatchObject({ Auth: { Cognito: { region: "eu-west-2" } } });
        });
    });

    describe("signIn", () => {
        it("uses USER_SRP_AUTH and resolves DONE once tokens are visible", async () => {
            vi.mocked(signIn).mockResolvedValue({
                isSignedIn: true,
                nextStep: { signInStep: "DONE" }
            } as never);

            await expect(provider.signIn("u", "p")).resolves.toEqual({ step: SignInStep.DONE });

            expect(signIn).toHaveBeenCalledWith({
                username: "u",
                password: "p",
                options: { authFlowType: "USER_SRP_AUTH" }
            });
        });

        it("maps CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED to NEW_PASSWORD_REQUIRED without touching the session", async () => {
            vi.mocked(signIn).mockResolvedValue({
                isSignedIn: false,
                nextStep: { signInStep: "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED" }
            } as never);

            await expect(provider.signIn("u", "p")).resolves.toEqual({ step: SignInStep.NEW_PASSWORD_REQUIRED });
            expect(fetchAuthSession).not.toHaveBeenCalled();
        });

        it("maps CONFIRM_SIGN_IN_WITH_TOTP_CODE to TOTP_CODE", async () => {
            vi.mocked(signIn).mockResolvedValue({
                isSignedIn: false,
                nextStep: { signInStep: "CONFIRM_SIGN_IN_WITH_TOTP_CODE" }
            } as never);

            await expect(provider.signIn("u", "p")).resolves.toEqual({ step: SignInStep.TOTP_CODE });
        });

        it("maps CONTINUE_SIGN_IN_WITH_TOTP_SETUP to TOTP_SETUP, labelling the authenticator entry with the username", async () => {
            const setupUri = new URL("otpauth://totp/FLIP:u@e.com?secret=ABCD");
            const getSetupUri = vi.fn(() => setupUri);
            vi.mocked(signIn).mockResolvedValue({
                isSignedIn: false,
                nextStep: {
                    signInStep: "CONTINUE_SIGN_IN_WITH_TOTP_SETUP",
                    totpSetupDetails: {
                        sharedSecret: "ABCD",  // pragma: allowlist secret
                        getSetupUri
                    }
                }
            } as never);

            await expect(provider.signIn("u@e.com", "p")).resolves.toEqual({
                step: SignInStep.TOTP_SETUP,
                totpSetup: {
                    sharedSecret: "ABCD",  // pragma: allowlist secret
                    setupUri: setupUri.toString()
                }
            });
            // The login username is the account label — passing the shared
            // secret here would leak it into the label the user sees.
            expect(getSetupUri).toHaveBeenCalledWith("FLIP", "u@e.com");
        });

        it("rejects with UNSUPPORTED when Amplify reports neither signed-in nor a known step", async () => {
            // Previously a silent no-op that left the user on the login page with
            // no feedback; surfacing it lets Login.vue show its error snackbar.
            vi.mocked(signIn).mockResolvedValue({
                isSignedIn: false,
                nextStep: { signInStep: "CONFIRM_SIGN_IN_WITH_SMS_CODE" }
            } as never);

            const err = await provider.signIn("u", "p").catch((e: unknown) => e);

            expect(err).toBeInstanceOf(AuthError);
            expect((err as AuthError).code).toBe("UNSUPPORTED");
            expect((err as AuthError).message).toContain("CONFIRM_SIGN_IN_WITH_SMS_CODE");
        });

        it("throws MISSING_SESSION_TOKENS when forceRefresh fails and no accessToken is available", async () => {
            // Amplify's forceRefresh can throw on its own (expired refresh
            // token, Cognito transient failure). The wait helper logs the
            // throw and the "no accessToken" warn, then throws a typed error
            // so the caller can surface a real message instead of letting
            // hydrate proceed unauthenticated and 401 generically.
            const consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
            const consoleWarnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
            vi.mocked(fetchAuthSession)
                .mockReset()
                .mockResolvedValueOnce(session(undefined))
                .mockRejectedValueOnce(new Error("Refresh token expired"));
            vi.mocked(signIn).mockResolvedValue({
                isSignedIn: true,
                nextStep: { signInStep: "DONE" }
            } as never);

            const err = await provider.signIn("u", "p").catch((e: unknown) => e);

            expect(err).toBeInstanceOf(AuthError);
            expect((err as AuthError).code).toBe("MISSING_SESSION_TOKENS");
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
            // first post-signIn backend call.
            vi.mocked(fetchAuthSession)
                .mockReset()
                .mockResolvedValueOnce(session(undefined))
                .mockResolvedValueOnce(session("access"));
            vi.mocked(signIn).mockResolvedValue({
                isSignedIn: true,
                nextStep: { signInStep: "DONE" }
            } as never);

            await expect(provider.signIn("u", "p")).resolves.toEqual({ step: SignInStep.DONE });

            expect(fetchAuthSession).toHaveBeenCalledTimes(2);
            expect(fetchAuthSession).toHaveBeenNthCalledWith(2, { forceRefresh: true });
        });

        it("signs the stale session out and retries on UserAlreadyAuthenticatedException", async () => {
            // Amplify v6 throws this when local storage already holds Cognito
            // tokens — e.g. the user typed credentials in /login while another
            // tab still has a live session. Recover by signing the stale
            // session out and retrying once, otherwise the user is stuck on
            // the login page until they clear storage by hand.
            const stale = Object.assign(new Error("There is already a signed in user."), { name: "UserAlreadyAuthenticatedException" });
            vi.mocked(signIn)
                .mockRejectedValueOnce(stale)
                .mockResolvedValueOnce({
                    isSignedIn: true,
                    nextStep: { signInStep: "DONE" }
                } as never);
            vi.mocked(signOut).mockResolvedValue(undefined as never);

            await expect(provider.signIn("u", "p")).resolves.toEqual({ step: SignInStep.DONE });

            expect(signIn).toHaveBeenCalledTimes(2);
            expect(signOut).toHaveBeenCalledTimes(1);
            // signOut must run between the two signIn attempts; otherwise the
            // retry hits the same exception in a loop.
            const signOutOrder = vi.mocked(signOut).mock.invocationCallOrder[0];
            const signInOrders = vi.mocked(signIn).mock.invocationCallOrder;
            expect(signOutOrder).toBeGreaterThan(signInOrders[0]);
            expect(signOutOrder).toBeLessThan(signInOrders[1]);
        });

        it("propagates UserAlreadyAuthenticatedException from the retry without a second retry", async () => {
            // Single-retry contract: two attempts max, no infinite loop.
            const stale = Object.assign(new Error("There is already a signed in user."), { name: "UserAlreadyAuthenticatedException" });
            vi.mocked(signIn).mockRejectedValueOnce(stale).mockRejectedValueOnce(stale);
            vi.mocked(signOut).mockResolvedValue(undefined as never);

            await expect(provider.signIn("u", "p")).rejects.toMatchObject({ name: "UserAlreadyAuthenticatedException" });

            expect(signIn).toHaveBeenCalledTimes(2);
            expect(signOut).toHaveBeenCalledTimes(1);
        });

        it("does not retry on errors other than UserAlreadyAuthenticatedException", async () => {
            const wrongPassword = Object.assign(new Error("Incorrect username or password."), { name: "NotAuthorizedException" });
            vi.mocked(signIn).mockRejectedValue(wrongPassword);

            await expect(provider.signIn("u", "p")).rejects.toThrow("Incorrect username or password.");

            expect(signIn).toHaveBeenCalledTimes(1);
            expect(signOut).not.toHaveBeenCalled();
        });
    });

    describe("confirmNewPassword", () => {
        it("answers the challenge and resolves DONE when signed in", async () => {
            vi.mocked(confirmSignIn).mockResolvedValue({
                isSignedIn: true,
                nextStep: { signInStep: "DONE" }
            } as never);

            await expect(provider.confirmNewPassword("newPassword123!")).resolves.toEqual({ step: SignInStep.DONE });

            expect(confirmSignIn).toHaveBeenCalledWith({ challengeResponse: "newPassword123!" });
            expect(fetchAuthSession).toHaveBeenCalled();
        });

        it("chains into TOTP_SETUP with the setup details when Cognito demands enrolment next", async () => {
            const setupUri = new URL("otpauth://totp/FLIP:u?secret=XYZ");
            vi.mocked(signIn).mockResolvedValue({
                isSignedIn: false,
                nextStep: { signInStep: "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED" }
            } as never);
            vi.mocked(confirmSignIn).mockResolvedValue({
                isSignedIn: false,
                nextStep: {
                    signInStep: "CONTINUE_SIGN_IN_WITH_TOTP_SETUP",
                    totpSetupDetails: {
                        sharedSecret: "XYZ",  // pragma: allowlist secret
                        getSetupUri: vi.fn(() => setupUri)
                    }
                }
            } as never);

            // The username captured at signIn is still the label for the chained step.
            await provider.signIn("u@e.com", "temp");
            await expect(provider.confirmNewPassword("newPassword123!")).resolves.toEqual({
                step: SignInStep.TOTP_SETUP,
                totpSetup: {
                    sharedSecret: "XYZ",  // pragma: allowlist secret
                    setupUri: setupUri.toString()
                }
            });
        });
    });

    describe("confirmTotpChallenge", () => {
        it("answers the challenge and resolves DONE once tokens are visible", async () => {
            vi.mocked(confirmSignIn).mockResolvedValue({
                isSignedIn: true,
                nextStep: { signInStep: "DONE" }
            } as never);

            await expect(provider.confirmTotpChallenge("123456")).resolves.toEqual({ step: SignInStep.DONE });
            expect(confirmSignIn).toHaveBeenCalledWith({ challengeResponse: "123456" });
        });

        it("passes a wrong-code rejection through untouched so the page's message heuristics work", async () => {
            const mismatch = Object.assign(new Error("Invalid code received for user"), { name: "CodeMismatchException" });
            vi.mocked(confirmSignIn).mockRejectedValue(mismatch);

            await expect(provider.confirmTotpChallenge("000000")).rejects.toBe(mismatch);
        });
    });

    describe("confirmTotpSetup", () => {
        it("verifies the code, then records TOTP as the preferred MFA, then resolves DONE", async () => {
            vi.mocked(confirmSignIn).mockResolvedValue({
                isSignedIn: true,
                nextStep: { signInStep: "DONE" }
            } as never);
            vi.mocked(updateMFAPreference).mockResolvedValue(undefined as never);

            await expect(provider.confirmTotpSetup("123456")).resolves.toEqual({ step: SignInStep.DONE });

            expect(confirmSignIn).toHaveBeenCalledWith({ challengeResponse: "123456" });
            expect(updateMFAPreference).toHaveBeenCalledWith({ totp: "PREFERRED" });
            expect(vi.mocked(updateMFAPreference).mock.invocationCallOrder[0])
                .toBeGreaterThan(vi.mocked(confirmSignIn).mock.invocationCallOrder[0]);
        });

        it("throws MFA_PREFERENCE_FAILED (carrying Cognito's message) when the preference does not stick", async () => {
            // Cognito accepted the verification code but the preference
            // didn't stick — the user is *not* MFA-enabled despite a clean
            // confirmSignIn. A typed code lets the store reset its state
            // rather than paint mfaEnabled=true and 403 every API call.
            vi.mocked(confirmSignIn).mockResolvedValue({
                isSignedIn: true,
                nextStep: { signInStep: "DONE" }
            } as never);
            vi.mocked(updateMFAPreference).mockRejectedValue(new Error("Cognito boom"));

            const err = await provider.confirmTotpSetup("123456").catch((e: unknown) => e);

            expect(err).toBeInstanceOf(AuthError);
            expect((err as AuthError).code).toBe("MFA_PREFERENCE_FAILED");
            expect((err as AuthError).message).toBe("Cognito boom");
        });

        it("does not touch the MFA preference when the code is rejected", async () => {
            const mismatch = Object.assign(new Error("Invalid code"), { name: "CodeMismatchException" });
            vi.mocked(confirmSignIn).mockRejectedValue(mismatch);

            await expect(provider.confirmTotpSetup("000000")).rejects.toBe(mismatch);
            expect(updateMFAPreference).not.toHaveBeenCalled();
        });
    });

    describe("setUpTotp / verifyTotpSetup (post-auth enrolment)", () => {
        it("setUpTotp mints a secret and labels the URI with the given account label", async () => {
            const url = new URL("otpauth://totp/FLIP:e@f.com?secret=SEC");
            const getSetupUri = vi.fn(() => url);
            vi.mocked(setUpTOTP).mockResolvedValue({
                sharedSecret: "SEC",  // pragma: allowlist secret
                getSetupUri
            } as never);

            await expect(provider.setUpTotp("e@f.com")).resolves.toEqual({
                sharedSecret: "SEC",  // pragma: allowlist secret
                setupUri: url.toString()
            });
            expect(getSetupUri).toHaveBeenCalledWith("FLIP", "e@f.com");
        });

        it("setUpTotp falls back to an empty secret and no label when Amplify omits them", async () => {
            const url = new URL("otpauth://totp/FLIP");
            const getSetupUri = vi.fn(() => url);
            vi.mocked(setUpTOTP).mockResolvedValue({
                sharedSecret: undefined,
                getSetupUri
            } as never);

            await expect(provider.setUpTotp()).resolves.toEqual({
                sharedSecret: "",
                setupUri: url.toString()
            });
            expect(getSetupUri).toHaveBeenCalledWith("FLIP", undefined);
        });

        it("verifyTotpSetup verifies the code then records the preference", async () => {
            vi.mocked(verifyTOTPSetup).mockResolvedValue(undefined as never);
            vi.mocked(updateMFAPreference).mockResolvedValue(undefined as never);

            await expect(provider.verifyTotpSetup("123456")).resolves.toBeUndefined();

            expect(verifyTOTPSetup).toHaveBeenCalledWith({ code: "123456" });
            expect(updateMFAPreference).toHaveBeenCalledWith({ totp: "PREFERRED" });
        });

        it("verifyTotpSetup propagates a wrong code without touching the preference", async () => {
            vi.mocked(verifyTOTPSetup).mockRejectedValue(new Error("Invalid code"));

            await expect(provider.verifyTotpSetup("000000")).rejects.toThrow("Invalid code");
            expect(updateMFAPreference).not.toHaveBeenCalled();
        });

        it("verifyTotpSetup throws MFA_PREFERENCE_FAILED when the preference does not stick", async () => {
            vi.mocked(verifyTOTPSetup).mockResolvedValue(undefined as never);
            vi.mocked(updateMFAPreference).mockRejectedValue(new Error("Boom"));

            const err = await provider.verifyTotpSetup("123456").catch((e: unknown) => e);

            expect((err as AuthError).code).toBe("MFA_PREFERENCE_FAILED");
            expect((err as AuthError).message).toBe("Boom");
        });
    });

    describe("getAccessToken", () => {
        it("returns the access token from the current session", async () => {
            vi.mocked(fetchAuthSession).mockResolvedValue(session("token-abc"));

            await expect(provider.getAccessToken()).resolves.toBe("token-abc");
            expect(fetchAuthSession).toHaveBeenCalledWith();
        });

        it("forces a refresh when the first read has no token", async () => {
            // Amplify v6 caches tokens asynchronously after signIn; a call
            // immediately after an `isSignedIn=true` resolve can observe an
            // empty session.
            vi.mocked(fetchAuthSession)
                .mockResolvedValueOnce(session(undefined))
                .mockResolvedValueOnce(session("fresh"));

            await expect(provider.getAccessToken()).resolves.toBe("fresh");
            expect(fetchAuthSession).toHaveBeenNthCalledWith(2, { forceRefresh: true });
        });

        it("goes straight to forceRefresh when asked", async () => {
            vi.mocked(fetchAuthSession).mockResolvedValue(session("fresh"));

            await expect(provider.getAccessToken({ forceRefresh: true })).resolves.toBe("fresh");
            expect(fetchAuthSession).toHaveBeenCalledTimes(1);
            expect(fetchAuthSession).toHaveBeenCalledWith({ forceRefresh: true });
        });

        it("returns null and logs a warning when forceRefresh throws, so the cause is visible in DevTools", async () => {
            // Without the warn log, the user would just be signed out by the
            // 401 handler with no clue which Amplify class fired
            // (TooManyRequestsException, network error, etc.).
            vi.mocked(fetchAuthSession)
                .mockResolvedValueOnce(session(undefined))
                .mockRejectedValueOnce(Object.assign(new Error("throttle"), { name: "TooManyRequestsException" }));
            const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});

            await expect(provider.getAccessToken()).resolves.toBeNull();

            expect(consoleWarn).toHaveBeenCalledWith(
                "Token forceRefresh failed:",
                expect.objectContaining({ name: "TooManyRequestsException" })
            );
            consoleWarn.mockRestore();
        });

        it("returns null when neither read yields a token", async () => {
            vi.mocked(fetchAuthSession).mockResolvedValue(session(undefined));

            await expect(provider.getAccessToken()).resolves.toBeNull();
        });
    });

    describe("getUser", () => {
        it("combines getCurrentUser with the user attributes", async () => {
            vi.mocked(getCurrentUser).mockResolvedValue({
                username: "u",
                userId: "id"
            } as never);
            vi.mocked(fetchUserAttributes).mockResolvedValue({
                sub: "s",
                email: "e@f.com"
            } as never);

            await expect(provider.getUser()).resolves.toEqual({
                sub: "s",
                email: "e@f.com",
                username: "u"
            });
        });

        it("falls back to userId when the sub attribute is missing", async () => {
            vi.mocked(getCurrentUser).mockResolvedValue({
                username: "u",
                userId: "id"
            } as never);
            vi.mocked(fetchUserAttributes).mockResolvedValue({ email: "e@f.com" } as never);

            await expect(provider.getUser()).resolves.toMatchObject({ sub: "id" });
        });

        it("propagates Amplify's rejection when signed out", async () => {
            vi.mocked(getCurrentUser).mockRejectedValue(new Error("The user is not authenticated"));

            await expect(provider.getUser()).rejects.toThrow("not authenticated");
        });
    });

    describe("hasSession", () => {
        it("is true only when the session carries an access token", async () => {
            vi.mocked(fetchAuthSession).mockResolvedValueOnce(session("tok"));
            await expect(provider.hasSession()).resolves.toBe(true);

            // A stale challenge session resolves without tokens and without
            // throwing — that is NOT a session.
            vi.mocked(fetchAuthSession).mockResolvedValueOnce(session(undefined));
            await expect(provider.hasSession()).resolves.toBe(false);
        });

        it("is false when fetchAuthSession throws", async () => {
            vi.mocked(fetchAuthSession).mockRejectedValueOnce(new Error("No current user"));

            await expect(provider.hasSession()).resolves.toBe(false);
        });
    });

    describe("signOut", () => {
        it("calls Cognito's GlobalSignOut when asked for a global sign-out", async () => {
            vi.mocked(signOut).mockResolvedValue(undefined as never);

            await expect(provider.signOut({ global: true })).resolves.toBeUndefined();
            expect(signOut).toHaveBeenCalledWith({ global: true });
        });

        it("signs out locally only by default", async () => {
            vi.mocked(signOut).mockResolvedValue(undefined as never);

            await provider.signOut();
            expect(signOut).toHaveBeenCalledWith({ global: false });
        });

        it("maps NotAuthorizedException (tokens already invalid) to SESSION_ALREADY_ENDED", async () => {
            const revoked = Object.assign(new Error("Access Token has been revoked"), { name: "NotAuthorizedException" });
            vi.mocked(signOut).mockRejectedValue(revoked);

            const err = await provider.signOut({ global: true }).catch((e: unknown) => e);

            expect(err).toBeInstanceOf(AuthError);
            expect((err as AuthError).code).toBe("SESSION_ALREADY_ENDED");
            expect((err as AuthError).cause).toBe(revoked);
        });

        it("passes any other failure through", async () => {
            const network = new Error("network");
            vi.mocked(signOut).mockRejectedValue(network);

            await expect(provider.signOut({ global: true })).rejects.toBe(network);
        });
    });

    describe("password reset", () => {
        it("resetPassword proxies to Amplify with web-app client metadata", async () => {
            vi.mocked(resetPassword).mockResolvedValue({ isPasswordReset: false } as never);

            await expect(provider.resetPassword("u@e.com")).resolves.toBeUndefined();

            expect(resetPassword).toHaveBeenCalledWith({
                username: "u@e.com",
                options: { clientMetadata: { source: "web-app" } }
            });
        });

        it("confirmResetPassword proxies to Amplify with web-app client metadata", async () => {
            vi.mocked(confirmResetPassword).mockResolvedValue(undefined as never);

            await provider.confirmResetPassword({
                email: "u@e.com",
                code: "123456",
                newPassword: "new-pw!"  // pragma: allowlist secret
            });

            expect(confirmResetPassword).toHaveBeenCalledWith({
                username: "u@e.com",
                confirmationCode: "123456",
                newPassword: "new-pw!",  // pragma: allowlist secret
                options: { clientMetadata: { source: "web-app" } }
            });
        });
    });

    describe("onSessionExpired", () => {
        it("listens on Amplify's auth Hub channel and forwards tokenRefresh_failure only", () => {
            const cb = vi.fn();
            const stop = vi.fn();
            vi.mocked(Hub.listen).mockReturnValue(stop as never);

            const unsubscribe = provider.onSessionExpired(cb);

            expect(Hub.listen).toHaveBeenCalledWith("auth", expect.any(Function));
            const listener = vi.mocked(Hub.listen).mock.calls[0][1] as (data: { payload: { event: string } }) => void;

            listener({ payload: { event: "signedIn" } });
            expect(cb).not.toHaveBeenCalled();

            listener({ payload: { event: "tokenRefresh_failure" } });
            expect(cb).toHaveBeenCalledTimes(1);

            unsubscribe();
            expect(stop).toHaveBeenCalledTimes(1);
        });
    });
});
