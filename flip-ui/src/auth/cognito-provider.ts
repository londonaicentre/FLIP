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

/**
 * AWS Cognito provider over aws-amplify v6 — the stag/prod backend.
 *
 * This is the ONLY module in src/ that imports `aws-amplify`. Every Amplify
 * quirk the app has learned the hard way (the post-signIn token race, the
 * UserAlreadyAuthenticated retry, the MFA-preference write after enrolment)
 * lives here, behind the neutral `AuthProvider` contract.
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

import { AuthCapabilities,
    AuthError,
    AuthProvider,
    AuthUser,
    SignInResult,
    SignInStep,
    TotpSetupDetails } from "./provider";

// Amplify v6 can resolve `signIn({isSignedIn: true})` a beat before
// `fetchAuthSession()` sees the cached tokens (the token-orchestrator
// write and the session-reader read don't share a barrier on every
// platform). When that happens, the very next backend request from
// `hydrate()` goes out without an `Authorization` header and 401s,
// which Login.vue surfaces as "There was a problem logging you in".
// Pause until either the tokens appear or a forceRefresh produces
// them, so callers can assume `hydrate()` sees a real session.
//
// Throws if no accessToken can be obtained — the caller's catch handler
// can then surface a real error to the user instead of silently
// proceeding to hydrate which will 401.
const waitForSessionTokens = async (): Promise<void> => {
    let session = await fetchAuthSession();
    if (session.tokens?.accessToken) return;
    try {
        session = await fetchAuthSession({ forceRefresh: true });
    } catch (e) {
        console.error("waitForSessionTokens: forceRefresh threw:", e);
    }
    if (!session.tokens?.accessToken) {
        // Log what Amplify *thinks* the session is so DevTools can
        // distinguish "no session at all" (bad storage / misconfigured
        // client) from "session exists but tokens are empty"
        // (token-refresh issue), then throw so the caller surfaces it.
        console.warn(
            "waitForSessionTokens: no accessToken after forceRefresh",
            {
                userSub: session.userSub,
                hasCredentials: !!session.credentials
            }
        );
        throw new AuthError(
            "MISSING_SESSION_TOKENS",
            "Authenticated but no session tokens — local storage may be blocked or your session has expired."
        );
    }
};

// Shape of Amplify's `nextStep` when Cognito chains into MFA_SETUP.
type AmplifyTotpNextStep = {
    totpSetupDetails?: {
        sharedSecret: string;
        getSetupUri: (appName: string, username?: string) => URL;
    };
};

// The subset of Amplify's SignInOutput / ConfirmSignInOutput this provider reads.
type AmplifySignInOutput = {
    isSignedIn: boolean;
    nextStep?: { signInStep?: string } & AmplifyTotpNextStep;
};

type Attributes = {
    sub?: string;
    email?: string;
};

// Cognito's MFA_SETUP challenge verifies the software token but does
// not flip the user's MFA preference; without an explicit
// `updateMFAPreference` the backend's /users/me/mfa/status still
// reads an empty UserMFASettingList and the router guard bounces
// the user back to enrol with a fresh secret.
//
// A failure here is fatal-to-enrolment: Cognito has the verified token
// but the preference didn't stick, so the user is *not* MFA-enabled
// despite a clean code check. It is surfaced as a typed error (carrying
// Cognito's own message for the page's snackbar) so the store can reset
// its state rather than paint `mfaEnabled=true` and let the user into an
// app where every API call 403s under the app-gate.
const recordTotpPreference = async (): Promise<void> => {
    try {
        await updateMFAPreference({ totp: "PREFERRED" });
    } catch (e) {
        throw new AuthError("MFA_PREFERENCE_FAILED", (e as Error)?.message ?? String(e), { cause: e });
    }
};

export class CognitoAuthProvider implements AuthProvider {
    readonly backend = "cognito" as const;

    readonly capabilities: AuthCapabilities = {
        newPasswordChallenge: true,
        totpChallenge: true,
        totpEnrolment: true,
        forgotPassword: true,
        adminResetPassword: true,
        globalSignOut: true
    };

    // Username captured at sign-in, used as the account label when building
    // the TOTP setup URI mid-challenge (before any user identity is readable).
    private pendingUsername: string | null = null;

    // Read Cognito config at runtime from window.* (populated by
    // public/js/window.js in dev and dist/js/window.js in prod — both
    // loaded synchronously before main.ts). The generator reads from
    // AWS_COGNITO_USER_POOL_ID / AWS_COGNITO_APP_CLIENT_ID / AWS_REGION,
    // matching the rest of the stack; no VITE_AWS_* duplication.
    configure(): void {
        // Built as a value first: `region` is not in Amplify v6's typed
        // Cognito config (the pool id carries it) but has always been passed
        // here, and an inline literal would fail the excess-property check.
        const authConfig = {
            Auth: {
                Cognito: {
                    region: window.AWS_REGION || "eu-west-2",
                    userPoolId: window.AWS_USER_POOL_ID,
                    userPoolClientId: window.AWS_CLIENT_ID
                }
            }
        };
        Amplify.configure(authConfig);
    }

    // `opts.totp` is not used: Cognito never takes a TOTP with the password,
    // it asks for one through the TOTP_CODE challenge.
    async signIn(username: string, password: string): Promise<SignInResult> {
        this.pendingUsername = username;

        // USER_SRP_AUTH so the browser sends an SRP proof rather
        // than the plaintext password — any TLS terminator, WAF, or
        // proxy that captures request bodies on the InitiateAuth
        // call cannot recover credentials. Amplify v6's default is
        // SRP; we set authFlowType explicitly to make the choice
        // visible at the call site rather than implicit in the
        // Amplify default.
        const credentials = {
            username,
            password,
            options: { authFlowType: "USER_SRP_AUTH" as const }
        };
        // Amplify throws UserAlreadyAuthenticatedException when storage
        // already holds tokens — e.g. credentials typed at /login while
        // another tab still has a live session. Sign the stale session
        // out and retry once so the user isn't stuck behind a generic
        // "problem logging you in" snackbar.
        let out: AmplifySignInOutput;
        try {
            out = await signIn(credentials);
        } catch (e) {
            if ((e as Error).name !== "UserAlreadyAuthenticatedException") throw e;
            await signOut();
            out = await signIn(credentials);
        }

        return this.mapSignInOutput(out);
    }

    async confirmNewPassword(newPassword: string): Promise<SignInResult> {
        const out = await confirmSignIn({ challengeResponse: newPassword });

        return this.mapSignInOutput(out);
    }

    async confirmTotpChallenge(code: string): Promise<SignInResult> {
        const out = await confirmSignIn({ challengeResponse: code });

        return this.mapSignInOutput(out);
    }

    async confirmTotpSetup(code: string): Promise<SignInResult> {
        const out = await confirmSignIn({ challengeResponse: code });
        // Once `confirmSignIn` resolves with `isSignedIn=true` the code was
        // already accepted by Cognito — any subsequent failure is a
        // post-success cleanup issue, not a code mismatch.
        const result = await this.mapSignInOutput(out);
        if (result.step === SignInStep.DONE) {
            await recordTotpPreference();
        }

        return result;
    }

    async setUpTotp(accountLabel?: string): Promise<TotpSetupDetails> {
        const details = await setUpTOTP();

        return {
            sharedSecret: details.sharedSecret ?? "",
            setupUri: details.getSetupUri("FLIP", accountLabel).toString()
        };
    }

    async verifyTotpSetup(code: string): Promise<void> {
        // `verifyTOTPSetup` is the code-mismatch site: if the user's
        // code is wrong this throws and the caller shows "Invalid
        // code". Everything after is post-success cleanup.
        await verifyTOTPSetup({ code });
        await recordTotpPreference();
    }

    async getAccessToken(opts?: { forceRefresh?: boolean }): Promise<string | null> {
        // Amplify v6 caches tokens asynchronously after signIn;
        // a call to fetchAuthSession() immediately after an
        // `isSignedIn=true` resolve can observe an empty
        // session. If tokens aren't there yet, force a refresh
        // so freshly-signed-in users don't hit a 401 on the
        // very next request (e.g. getMfaStatus from hydrate).
        let token: string | undefined;
        if (!opts?.forceRefresh) {
            const session = await fetchAuthSession();
            token = session.tokens?.accessToken?.toString();
        }
        if (!token) {
            try {
                const session = await fetchAuthSession({ forceRefresh: true });
                token = session.tokens?.accessToken?.toString();
            } catch (e) {
                // The request will go out unauthenticated and
                // the 401 handler will force a sign-out, but
                // we log the underlying Amplify error so
                // DevTools surfaces *why* (throttle, expired
                // refresh token, storage blocked) instead of
                // collapsing every cause to "signed out".
                console.warn("Token forceRefresh failed:", e);
            }
        }

        return token ?? null;
    }

    async getUser(): Promise<AuthUser> {
        const [{ username, userId }, attributes] = await Promise.all([
            getCurrentUser(),
            fetchUserAttributes() as Promise<Attributes>
        ]);

        return {
            sub: attributes.sub ?? userId,
            email: attributes.email ?? "",
            username
        };
    }

    // Only a session with a real access token counts. `fetchAuthSession`
    // can return a session object (e.g. with a stale challenge string)
    // without tokens and without throwing; treating that as "signed in"
    // is what used to make "Back to log in" from mid-challenge pages
    // bounce straight back to the challenge page via the router guard.
    async hasSession(): Promise<boolean> {
        try {
            const session = await fetchAuthSession();

            return Boolean(session.tokens?.accessToken);
        } catch {
            return false;
        }
    }

    // global:true calls Cognito's GlobalSignOut, which invalidates
    // all tokens (including the refresh token) for this user session.
    async signOut(opts?: { global?: boolean }): Promise<void> {
        try {
            await signOut({ global: opts?.global ?? false });
        } catch (error) {
            // NotAuthorizedException means the tokens were already invalid
            // (revoked elsewhere, e.g. by an admin MFA reset) — the session
            // had ended before we asked. Anything else is a real failure
            // the store must warn about.
            if ((error as { name?: string }).name === "NotAuthorizedException") {
                throw new AuthError("SESSION_ALREADY_ENDED", (error as Error).message, { cause: error });
            }
            throw error;
        }
    }

    async resetPassword(email: string): Promise<void> {
        await resetPassword({
            username: email,
            options: { clientMetadata: { source: "web-app" } }
        });
    }

    async confirmResetPassword(args: { email: string; code: string; newPassword: string }): Promise<void> {
        await confirmResetPassword({
            username: args.email,
            confirmationCode: args.code,
            newPassword: args.newPassword,
            options: { clientMetadata: { source: "web-app" } }
        });
    }

    // Amplify emits `tokenRefresh_failure` on its Hub when Cognito rejects
    // the refresh token — the production "your session has expired" signal.
    onSessionExpired(cb: () => void): () => void {
        return Hub.listen("auth", (data: { payload: { event: string } }) => {
            if (data.payload.event === "tokenRefresh_failure") {
                cb();
            }
        });
    }

    // Map Amplify's `nextStep.signInStep` onto the neutral steps. A step this
    // app does not implement (SMS / email OTP, custom challenges) — or a
    // "not signed in, no step" response — is an error, not a silent no-op:
    // leaving the user on the login form with no feedback was the old
    // behaviour and it read as "nothing happened".
    private async mapSignInOutput(out: AmplifySignInOutput): Promise<SignInResult> {
        const step = out.nextStep?.signInStep;
        switch (step) {
            case "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED":
                return { step: SignInStep.NEW_PASSWORD_REQUIRED };
            case "CONFIRM_SIGN_IN_WITH_TOTP_CODE":
                return { step: SignInStep.TOTP_CODE };
            case "CONTINUE_SIGN_IN_WITH_TOTP_SETUP":
                return {
                    step: SignInStep.TOTP_SETUP,
                    totpSetup: this.captureTotpSetupDetails(out.nextStep)
                };
        }
        if (out.isSignedIn) {
            await waitForSessionTokens();

            return { step: SignInStep.DONE };
        }
        throw new AuthError("UNSUPPORTED", `Unsupported Cognito sign-in step: ${step ?? "none (not signed in)"}`);
    }

    private captureTotpSetupDetails(nextStep: AmplifyTotpNextStep | undefined): TotpSetupDetails {
        const details = nextStep?.totpSetupDetails;
        if (!details) {
            throw new AuthError("UNSUPPORTED", "Cognito asked for TOTP setup but sent no setup details.");
        }
        // Use the login username (email) as the authenticator's
        // account label. Passing `sharedSecret` here would leak
        // the secret into the label the user sees in their app.
        // `pendingUsername` may be null for callers that didn't
        // route through signIn — fall back to omitting the label.
        const accountLabel = this.pendingUsername ?? undefined;

        return {
            sharedSecret: details.sharedSecret,
            setupUri: details.getSetupUri("FLIP", accountLabel).toString()
        };
    }
}
