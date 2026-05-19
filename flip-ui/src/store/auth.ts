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
import { defineStore } from "pinia";

import { IChangePassword } from "@/interfaces/auth/interfaces";
import { routeChange } from "@/router";
import { getMfaStatus, getUserPermissions } from "@/services/user-service";
import { Snackbar } from "@/utils/snackbar";

/**
 * Available User Permissions
 */
export type UserPermissions =
    | "CanManageUsers"
    | "CanManageProjects"
    | "CanApproveProjects"
    | "CanDeleteAnyProject"
    | "CanUnstageProjects"
    | "CanManageSiteBanner"
    | "CanManageDeployments"
    | "CanAccessAdminPanel";

type Attributes = {
    sub: string;
    email: string;
};

type AmplifyUser = {
    username: string;
    userId: string;
    attributes: Attributes;
    permissions: string[];
};

type UserCredentials = {
    username: string;
    password: string;
};

// DONE means Cognito's challenge chain is cleared — it does NOT imply the
// app-gate will let the user through (`mfaEnabled` decides that).
export type SignInStep =
    | "DONE"
    | "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED"
    | "CONTINUE_SIGN_IN_WITH_TOTP_SETUP"
    | "CONFIRM_SIGN_IN_WITH_TOTP_CODE";

// `setupUri` is an `otpauth://` URL for QR rendering; `sharedSecret` is the
// base32 secret users can type manually.
type TotpSetupDetails = {
    sharedSecret: string;
    setupUri: string;
};

type AuthState = {
    user: AmplifyUser | null;
    signInStep: SignInStep | null;
    totpSetup: TotpSetupDetails | null;
    // null = not yet known (store hydration in progress or sign-in incomplete)
    mfaEnabled: boolean | null;
    // Mirrors the backend's Settings.ENFORCE_MFA flag (sourced from
    // /users/me/mfa/status). null until the first hydrate lands; once
    // populated the router guard uses it to skip the /auth/mfa-setup
    // redirect in dev environments where MFA is not required.
    mfaRequired: boolean | null;
    // Username captured at sign-in, used as the account label when building
    // the TOTP setup URI before `user` is populated (challenge chain step).
    pendingUsername: string | null;
};

const buildUserWithPermissions = async (
    base: Pick<AmplifyUser, "username" | "userId">
): Promise<AmplifyUser> => {
    const [attributes, permsRes] = await Promise.all([
        fetchUserAttributes() as Promise<Attributes>,
        getUserPermissions(base.userId)
    ]);

    return {
        ...base,
        attributes,
        permissions: permsRes.permissions ?? []
    };
};

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
class MissingSessionTokensError extends Error {
    constructor(message: string) {
        super(message);
        this.name = "MissingSessionTokensError";
    }
}

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
        throw new MissingSessionTokensError(
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


export const useAuthStore = defineStore("auth", {
    state: (): AuthState => ({
        user: null,
        signInStep: null,
        totpSetup: null,
        mfaEnabled: null,
        mfaRequired: null,
        pendingUsername: null
    }),

    getters: {
        getUser: (state) => state.user,
        // Sign-in challenge chain complete AND (either the backend
        // doesn't require MFA in this environment, or TOTP is active).
        // `mfaRequired === false` covers the dev bypass; stag/prod have
        // mfaRequired=true and still need mfaEnabled=true.
        confirmedUser: (state) =>
            state.signInStep === "DONE" &&
            !!state.user &&
            (state.mfaRequired === false || state.mfaEnabled === true),
        // True only when Cognito challenges are cleared AND the backend
        // demands MFA AND the user hasn't enrolled yet — the one state
        // where the router routes them to the enrolment page. In dev
        // (mfaRequired=false) this always returns false.
        needsMfaEnrolment: (state) =>
            state.signInStep === "DONE" &&
            state.mfaRequired === true &&
            state.mfaEnabled === false
    },
    actions: {
        // Load user + MFA state into the store. When the MFA state is
        // already known (e.g. just after a successful TOTP enrolment),
        // the caller can pass it in as `{enabled, required}` to skip the
        // round-trip to /users/me/mfa/status. Post-enrolment callers
        // know both values, so the shortcut now takes an object rather
        // than a bare boolean.
        async hydrate(knownMfaState?: { enabled: boolean; required: boolean }) {
            const [{ username, userId }, mfaState] = await Promise.all([
                getCurrentUser(),
                knownMfaState !== undefined
                    ? Promise.resolve(knownMfaState)
                    : getMfaStatus()
            ]);
            this.signInStep = "DONE";
            this.mfaEnabled = mfaState.enabled;
            this.mfaRequired = mfaState.required;
            // Fetch permissions whenever the user has full API access —
            // i.e. MFA is active OR this environment doesn't require it.
            // The attributes-only branch is reserved for the "MFA
            // required but not yet enrolled" case, where the permissions
            // endpoint would 403 under the app-layer gate.
            if (mfaState.enabled || !mfaState.required) {
                this.user = await buildUserWithPermissions({
                    username,
                    userId
                });
            } else {
                const attributes = (await fetchUserAttributes()) as Attributes;
                this.user = {
                    username,
                    userId,
                    attributes,
                    permissions: []
                };
            }
        },

        async fetchInfo() {
            await this.hydrate();
        },

        async finaliseSignIn() {
            await this.hydrate();
        },

        async signIn(details: UserCredentials) {
            this.user = null;
            this.signInStep = null;
            this.totpSetup = null;
            this.mfaEnabled = null;
            this.mfaRequired = null;
            this.pendingUsername = details.username;

            // USER_SRP_AUTH so the browser sends an SRP proof rather
            // than the plaintext password — any TLS terminator, WAF, or
            // proxy that captures request bodies on the InitiateAuth
            // call cannot recover credentials. Amplify v6's default is
            // SRP; we set authFlowType explicitly to make the choice
            // visible at the call site rather than implicit in the
            // Amplify default.
            const credentials = {
                username: details.username,
                password: details.password,
                options: { authFlowType: "USER_SRP_AUTH" as const }
            };
            // Amplify throws UserAlreadyAuthenticatedException when storage
            // already holds tokens — e.g. credentials typed at /login while
            // another tab still has a live session. Sign the stale session
            // out and retry once so the user isn't stuck behind a generic
            // "problem logging you in" snackbar.
            let out;
            try {
                out = await signIn(credentials);
            } catch (e) {
                if ((e as Error).name !== "UserAlreadyAuthenticatedException") throw e;
                await signOut();
                out = await signIn(credentials);
            }

            const step = out.nextStep?.signInStep as SignInStep | undefined;
            if (step) {
                this.signInStep = step;
            }

            if (step === "CONTINUE_SIGN_IN_WITH_TOTP_SETUP") {
                this.captureTotpSetupDetails(out.nextStep);

                return;
            }

            if (
                step === "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED" ||
                step === "CONFIRM_SIGN_IN_WITH_TOTP_CODE"
            ) {
                return;
            }

            if (out.isSignedIn) {
                await waitForSessionTokens();
                try {
                    await this.hydrate();
                } catch (e) {
                    // hydrate rolls up any getCurrentUser / getMfaStatus
                    // failure after Cognito has already accepted the
                    // credentials. Log the underlying error (AxiosError
                    // status, Amplify class name, message, response
                    // body) so DevTools surfaces the real cause instead
                    // of Login.vue's generic "problem logging you in"
                    // snackbar swallowing it.
                    type AxiosErrorLike = {
                        response?: { status?: number; data?: unknown; headers?: unknown };
                        config?: { url?: string; headers?: unknown };
                        name?: string;
                    };
                    const ax = e as AxiosErrorLike;
                    console.error("Post-signIn hydrate failed:", e, {
                        name: ax.name,
                        status: ax.response?.status,
                        responseBody: ax.response?.data,
                        requestUrl: ax.config?.url,
                        requestHadAuth: Boolean(
                            (ax.config?.headers as Record<string, unknown> | undefined)
                                ?.Authorization
                        )
                    });
                    throw e;
                }
            }
        },

        async changePassword(newPassword: string) {
            const out = await confirmSignIn({ challengeResponse: newPassword });

            const step = out.nextStep?.signInStep as SignInStep | undefined;
            if (step) {
                this.signInStep = step;
            }

            if (step === "CONTINUE_SIGN_IN_WITH_TOTP_SETUP") {
                this.captureTotpSetupDetails(out.nextStep);

                return;
            }

            if (out.isSignedIn) {
                await waitForSessionTokens();
                await this.hydrate();
            }
        },

        async confirmTotpChallenge(code: string) {
            const out = await confirmSignIn({ challengeResponse: code });
            if (!out.isSignedIn) return;

            // Cognito only issues CONFIRM_SIGN_IN_WITH_TOTP_CODE to users
            // whose MFA preference is already active, so once the challenge
            // clears we know MFA is on — skip the backend round-trip.
            // As with confirmTotpSetup/completeMfaEnrolment, post-success
            // hydrate failures (network blip, backend unreachable) are
            // logged but non-fatal: the router guard will re-fetch on the
            // next navigation. Without this, a valid code + transient
            // hydrate failure surfaces to the user as "Sign-in failed:
            // Network Error" — misleadingly blaming the code.
            await waitForSessionTokens();
            try {
                await this.hydrate({
                    enabled: true,
                    required: true
                });
            } catch (e) {
                console.error("Failed to hydrate user post-TOTP-challenge:", e);
                this.signInStep = "DONE";
                this.mfaEnabled = null;
                // Surface the partial-success state. Without this, the user
                // sees a clean form submission and assumes sign-in worked,
                // while every subsequent API call will 401 until the router
                // guard re-fetches MFA status on the next navigation.
                Snackbar.show({
                    type: "warning",
                    title: "Sign-in needs another step",
                    text: "We couldn't finish loading your account. Please retry the page or sign in again."
                });
            }
        },

        // Cognito's MFA_SETUP challenge verifies the software token but does
        // not flip the user's MFA preference; without an explicit
        // `updateMFAPreference` the backend's /users/me/mfa/status still
        // reads an empty UserMFASettingList and the router guard bounces
        // the user back to enrol with a fresh secret.
        //
        // Once `confirmSignIn` resolves with `isSignedIn=true` the code was
        // already accepted by Cognito — any subsequent failure is a
        // post-success cleanup issue, not a code mismatch.
        //
        // updateMFAPreference failure is treated as fatal-to-enrolment and
        // re-thrown: Cognito has the verified token but the preference
        // didn't stick, so the user is *not* MFA-enabled despite a clean
        // confirmSignIn. Painting `mfaEnabled=true` would let them into
        // the app where every API call 403s under the app-gate. We reset
        // state and re-throw so the page does not navigate to /projects
        // on a half-failed enrolment; the page is responsible for the
        // user-facing snackbar (mfa-setup.vue's submit catch).
        // hydrate failure on its own is non-fatal — the router guard will
        // re-fetch on the next navigation.
        async confirmTotpSetup(code: string) {
            const out = await confirmSignIn({ challengeResponse: code });
            if (!out.isSignedIn) return;

            await waitForSessionTokens();
            // Clear the setup secret regardless of what happens below —
            // it has been used (Cognito accepted the code) and must not
            // be re-rendered as a QR.
            this.totpSetup = null;
            try {
                await updateMFAPreference({ totp: "PREFERRED" });
            } catch (e) {
                // Cognito accepted the verification code but the
                // preference didn't stick — the user is *not* MFA-enabled
                // despite a clean confirmSignIn. Reset state so the next
                // navigation re-fetches authoritative status, then rethrow
                // so the calling page's catch surfaces the error and
                // skips the navigate-to-/projects success path. The page
                // (mfa-setup.vue) shows the user-facing snackbar; we
                // don't double-notify here.
                console.error("Failed to set MFA preference post-setup:", e);
                this.signInStep = "DONE";
                this.mfaEnabled = null;
                throw e;
            }
            try {
                await this.hydrate({
                    enabled: true,
                    required: true
                });
            } catch (e) {
                console.error("Failed to hydrate user post-MFA setup:", e);
                // Leave `mfaEnabled=null` so the router guard re-fetches
                // the authoritative backend state on the next navigation
                // (via `fetchInfo()` at utils/auth.ts).
                this.signInStep = "DONE";
                this.mfaEnabled = null;
            }
        },

        captureTotpSetupDetails(nextStep: unknown) {
            const details = (nextStep as AmplifyTotpNextStep).totpSetupDetails;
            if (details) {
                // Use the login username (email) as the authenticator's
                // account label. Passing `sharedSecret` here would leak
                // the secret into the label the user sees in their app.
                // `pendingUsername` may be null for callers that didn't
                // route through signIn — fall back to omitting the label.
                const accountLabel = this.pendingUsername ?? undefined;
                this.totpSetup = {
                    sharedSecret: details.sharedSecret,
                    setupUri: details.getSetupUri("FLIP", accountLabel).toString()
                };
            }
        },

        async beginMfaEnrolment() {
            const details = await setUpTOTP();
            this.totpSetup = {
                sharedSecret: details.sharedSecret ?? "",
                setupUri: details.getSetupUri("FLIP", this.user?.attributes.email).toString()
            };
        },

        async completeMfaEnrolment(code: string) {
            // `verifyTOTPSetup` is the code-mismatch site: if the user's
            // code is wrong this throws and the caller shows "Invalid
            // code". Everything after is post-success cleanup.
            //
            // updateMFAPreference failure is fatal: see confirmTotpSetup
            // for the reasoning (Cognito has the verified token but the
            // preference didn't stick — a `mfaEnabled=true` shortcut
            // would 403 every subsequent API call). Surface, rethrow.
            await verifyTOTPSetup({ code });
            // Setup secret was just used; clear before further work so a
            // post-success failure can't leave it lingering for re-render.
            this.totpSetup = null;
            try {
                await updateMFAPreference({ totp: "PREFERRED" });
            } catch (e) {
                // See confirmTotpSetup: rethrow so the page can keep the
                // user on the form. Snackbar is the page's job.
                console.error("Failed to set MFA preference post-enrolment:", e);
                this.signInStep = "DONE";
                this.mfaEnabled = null;
                throw e;
            }
            try {
                await this.hydrate({
                    enabled: true,
                    required: true
                });
            } catch (e) {
                console.error("Failed to hydrate user post-MFA enrolment:", e);
                // See confirmTotpSetup: leave mfaEnabled=null so the
                // router guard converges on the real backend state.
                this.signInStep = "DONE";
                this.mfaEnabled = null;
            }
        },

        // `viaInterceptor=true` is set by the api.ts 401 handler. In that
        // path the interceptor already shows its own "Not Authorised"
        // snackbar, so we suppress sign-out feedback to avoid stacking
        // notifications. User-initiated sign-out (from MainLayout) leaves
        // it false: NotAuthorizedException then surfaces an info-level
        // notice so a session that was force-revoked remotely (e.g. by an
        // admin via MFA reset) doesn't disappear silently.
        async signOut(opts: { viaInterceptor?: boolean } = {}) {
            const { viaInterceptor = false } = opts;
            let serverSideSignOutFailed = false;
            let sessionAlreadyEnded = false;
            try {
                // global:true calls Cognito's GlobalSignOut, which invalidates
                // all tokens (including the refresh token) for this user session.
                await signOut({ global: true });
            } catch (error) {
                // Local state is wiped regardless so the user can't keep
                // using the app from this tab. But the server-side
                // refresh token may still be valid and replayable by
                // anything with access to where it was stored (XSS
                // payload, hostile extension), so warn the user — except
                // when the failure is just "tokens were already invalid"
                // (NotAuthorizedException). For interceptor-driven sign-
                // out that case stays silent; for user-clicked sign-out
                // we still want to inform them rather than silently mask
                // a remote revocation.
                console.error("Sign out error:", error);
                const name = (error as { name?: string }).name;
                if (name === "NotAuthorizedException") {
                    sessionAlreadyEnded = true;
                } else {
                    serverSideSignOutFailed = true;
                }
            }

            this.$reset();
            routeChange.gotoLogin();

            if (serverSideSignOutFailed) {
                Snackbar.error({
                    title: "Sign-out incomplete",
                    text: "We couldn't fully end your session on the server. Please close all browser windows for this site."
                });
            } else if (sessionAlreadyEnded && !viaInterceptor) {
                Snackbar.show({
                    type: "info",
                    title: "Session already ended",
                    text: "Your session had already ended (it may have been revoked elsewhere). You've been signed out."
                });
            }
        },

        async resetPassword(email: string) {
            const response = await resetPassword({
                username: email,
                options: { clientMetadata: { source: "web-app" } }
            });

            return response;
        },

        async updateForgottenPassword({ email, code, newPassword }: IChangePassword) {
            const response = await confirmResetPassword({
                username: email,
                confirmationCode: code,
                newPassword,
                options: { clientMetadata: { source: "web-app" } }
            });

            return response;
        },

        hasPermissions(permissions: UserPermissions[]) {
            return permissions.every(perm => this.user?.permissions?.includes(perm));
        }
    }
});
