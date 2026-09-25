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

import { defineStore } from "pinia";

import { getAuthProvider } from "@/auth";
import { AuthCapabilities,
    AuthError,
    AuthUser,
    SignInResult,
    SignInStep,
    TotpSetupDetails } from "@/auth/provider";
import { IChangePassword } from "@/interfaces/auth/interfaces";
import { getMfaStatus, getUserPermissions } from "@/services/user-service";
import { leaveToLogin, stashPostSignOutNotice } from "@/utils/session-teardown";
import { Snackbar } from "@/utils/snackbar";

// The step names are provider-neutral (FLIP#919) and owned by the auth seam;
// re-exported so `import { SignInStep } from "@/store/auth"` keeps working.
export { SignInStep };

/**
 * Available User Permissions
 */
export type UserPermissions =
    | "CanManageUsers"
    | "CanManageProjects"
    | "CanCreateProjects"
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

// The signed-in user as the rest of the app (and the Cypress
// `cypress.auth.user` fixture) sees it. The shape predates the provider
// seam and is kept: `userId` is the provider subject, `attributes` the
// display identity, `permissions` what flip-api grants.
export type AuthenticatedUser = {
    username: string;
    userId: string;
    attributes: Attributes;
    permissions: string[];
};

type UserCredentials = {
    username: string;
    password: string;
};

type AuthState = {
    user: AuthenticatedUser | null;
    // DONE means the provider's challenge chain is cleared — it does NOT
    // imply the app-gate will let the user through (`mfaEnabled` decides that).
    signInStep: SignInStep | null;
    totpSetup: TotpSetupDetails | null;
    // null = not yet known (store hydration in progress or sign-in incomplete)
    mfaEnabled: boolean | null;
    // Mirrors the backend's Settings.ENFORCE_MFA flag (sourced from
    // /users/me/mfa/status). null until the first hydrate lands; once
    // populated the router guard uses it to skip the /auth/mfa-setup
    // redirect in dev environments where MFA is not required.
    mfaRequired: boolean | null;
};

const toStoreUser = (identity: AuthUser, permissions: string[]): AuthenticatedUser => ({
    username: identity.username,
    userId: identity.sub,
    attributes: {
        sub: identity.sub,
        email: identity.email
    },
    permissions
});

export const useAuthStore = defineStore("auth", {
    state: (): AuthState => ({
        user: null,
        signInStep: null,
        totpSetup: null,
        mfaEnabled: null,
        mfaRequired: null
    }),

    getters: {
        getUser: (state) => state.user,
        // Sign-in challenge chain complete AND (either the backend
        // doesn't require MFA in this environment, or TOTP is active).
        // `mfaRequired === false` covers the dev bypass; stag/prod have
        // mfaRequired=true and still need mfaEnabled=true.
        confirmedUser: (state) =>
            state.signInStep === SignInStep.DONE &&
            !!state.user &&
            (state.mfaRequired === false || state.mfaEnabled === true),
        // True only when the provider's challenges are cleared AND the backend
        // demands MFA AND the user hasn't enrolled yet — the one state
        // where the router routes them to the enrolment page. In dev
        // (mfaRequired=false) this always returns false.
        needsMfaEnrolment: (state) =>
            state.signInStep === SignInStep.DONE &&
            state.mfaRequired === true &&
            state.mfaEnabled === false,
        // What the configured backend can do; pages guard their flows on
        // these rather than on the backend's name.
        capabilities: (): AuthCapabilities => getAuthProvider().capabilities
    },
    actions: {
        // Load user + MFA state into the store. When the MFA state is
        // already known (e.g. just after a successful TOTP enrolment),
        // the caller can pass it in as `{enabled, required}` to skip the
        // round-trip to /users/me/mfa/status. Post-enrolment callers
        // know both values, so the shortcut now takes an object rather
        // than a bare boolean.
        async hydrate(knownMfaState?: { enabled: boolean; required: boolean }) {
            const [identity, mfaState] = await Promise.all([
                getAuthProvider().getUser(),
                knownMfaState !== undefined
                    ? Promise.resolve(knownMfaState)
                    : getMfaStatus()
            ]);
            this.signInStep = SignInStep.DONE;
            this.mfaEnabled = mfaState.enabled;
            this.mfaRequired = mfaState.required;
            // Fetch permissions whenever the user has full API access —
            // i.e. MFA is active OR this environment doesn't require it.
            // The identity-only branch is reserved for the "MFA
            // required but not yet enrolled" case, where the permissions
            // endpoint would 403 under the app-layer gate.
            if (mfaState.enabled || !mfaState.required) {
                const permsRes = await getUserPermissions(identity.sub);
                this.user = toStoreUser(identity, permsRes.permissions ?? []);
            } else {
                this.user = toStoreUser(identity, []);
            }
        },

        async fetchInfo() {
            await this.hydrate();
        },

        async finaliseSignIn() {
            await this.hydrate();
        },

        // True when the provider holds a usable session — the router
        // guard's and Login.vue's question. Never throws.
        async hasSession(): Promise<boolean> {
            return getAuthProvider().hasSession();
        },

        // Record where the provider left the challenge chain and, once it
        // is cleared, load the user. Shared by signIn and changePassword.
        async applySignInResult(result: SignInResult) {
            this.signInStep = result.step;

            if (result.step === SignInStep.TOTP_SETUP) {
                this.totpSetup = result.totpSetup;

                return;
            }
            if (result.step !== SignInStep.DONE) {
                return;
            }

            try {
                await this.hydrate();
            } catch (e) {
                // hydrate rolls up any getUser / getMfaStatus failure
                // after the provider has already accepted the
                // credentials. Log the underlying error (AxiosError
                // status, SDK class name, message, response body) so
                // DevTools surfaces the real cause instead of
                // Login.vue's generic "problem logging you in"
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
        },

        async signIn(details: UserCredentials) {
            this.user = null;
            this.signInStep = null;
            this.totpSetup = null;
            this.mfaEnabled = null;
            this.mfaRequired = null;

            const result = await getAuthProvider().signIn(details.username, details.password);
            await this.applySignInResult(result);
        },

        async changePassword(newPassword: string) {
            const result = await getAuthProvider().confirmNewPassword(newPassword);
            await this.applySignInResult(result);
        },

        async confirmTotpChallenge(code: string) {
            const result = await getAuthProvider().confirmTotpChallenge(code);
            if (result.step !== SignInStep.DONE) {
                await this.applySignInResult(result);

                return;
            }

            // Cognito only issues the TOTP-code challenge to users whose MFA
            // preference is already active, so once the challenge clears we
            // know MFA is on — skip the backend round-trip.
            // As with confirmTotpSetup/completeMfaEnrolment, post-success
            // hydrate failures (network blip, backend unreachable) are
            // logged but non-fatal: the router guard will re-fetch on the
            // next navigation. Without this, a valid code + transient
            // hydrate failure surfaces to the user as "Sign-in failed:
            // Network Error" — misleadingly blaming the code.
            try {
                await this.hydrate({
                    enabled: true,
                    required: true
                });
            } catch (e) {
                console.error("Failed to hydrate user post-TOTP-challenge:", e);
                this.signInStep = SignInStep.DONE;
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

        // Sign-in-chain enrolment. The provider verifies the code AND records
        // TOTP as the preferred MFA; a failure of the second half comes back
        // as `MFA_PREFERENCE_FAILED` — the code was accepted (so the setup
        // secret is spent and must not be re-rendered as a QR) but the user
        // is *not* MFA-enabled. Reset state so the next navigation re-fetches
        // authoritative status, then rethrow so the calling page's catch
        // surfaces the error and skips the navigate-to-/projects success
        // path. The page (mfa-setup.vue) shows the user-facing snackbar; we
        // don't double-notify here. A wrong code rejects before any of that
        // and leaves the secret in place for a retry.
        // hydrate failure on its own is non-fatal — the router guard will
        // re-fetch on the next navigation.
        async confirmTotpSetup(code: string) {
            let result: SignInResult;
            try {
                result = await getAuthProvider().confirmTotpSetup(code);
            } catch (e) {
                if (e instanceof AuthError && e.code === "MFA_PREFERENCE_FAILED") {
                    console.error("Failed to set MFA preference post-setup:", e);
                    this.totpSetup = null;
                    this.signInStep = SignInStep.DONE;
                    this.mfaEnabled = null;
                }
                throw e;
            }
            // Clear the setup secret regardless of what happens below —
            // it has been used (the provider accepted the code) and must not
            // be re-rendered as a QR.
            this.totpSetup = null;
            if (result.step !== SignInStep.DONE) {
                await this.applySignInResult(result);

                return;
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
                this.signInStep = SignInStep.DONE;
                this.mfaEnabled = null;
            }
        },

        async beginMfaEnrolment() {
            this.totpSetup = await getAuthProvider().setUpTotp(this.user?.attributes.email);
        },

        // Post-auth enrolment: same contract as confirmTotpSetup (see there)
        // — a wrong code rejects with the secret intact, MFA_PREFERENCE_FAILED
        // means the code was accepted but the user is not MFA-enabled.
        async completeMfaEnrolment(code: string) {
            try {
                await getAuthProvider().verifyTotpSetup(code);
            } catch (e) {
                if (e instanceof AuthError && e.code === "MFA_PREFERENCE_FAILED") {
                    console.error("Failed to set MFA preference post-enrolment:", e);
                    this.totpSetup = null;
                    this.signInStep = SignInStep.DONE;
                    this.mfaEnabled = null;
                }
                throw e;
            }
            // Setup secret was just used; clear before further work so a
            // post-success failure can't leave it lingering for re-render.
            this.totpSetup = null;
            try {
                await this.hydrate({
                    enabled: true,
                    required: true
                });
            } catch (e) {
                console.error("Failed to hydrate user post-MFA enrolment:", e);
                // See confirmTotpSetup: leave mfaEnabled=null so the
                // router guard converges on the real backend state.
                this.signInStep = SignInStep.DONE;
                this.mfaEnabled = null;
            }
        },

        // `viaInterceptor=true` is set by the api.ts 401 handler. In that
        // path the interceptor already shows its own "Not Authorised"
        // snackbar, so we suppress sign-out feedback to avoid stacking
        // notifications. User-initiated sign-out (from MainLayout) leaves
        // it false: SESSION_ALREADY_ENDED then surfaces an info-level
        // notice so a session that was force-revoked remotely (e.g. by an
        // admin via MFA reset) doesn't disappear silently.
        async signOut(opts: { viaInterceptor?: boolean } = {}) {
            const { viaInterceptor = false } = opts;
            const provider = getAuthProvider();
            let serverSideSignOutFailed = false;
            let sessionAlreadyEnded = false;
            try {
                // A global sign-out revokes every session of the user
                // server-side (Cognito's GlobalSignOut, refresh token
                // included) where the backend supports it.
                await provider.signOut({ global: provider.capabilities.globalSignOut });
            } catch (error) {
                // Local state is wiped regardless so the user can't keep
                // using the app from this tab. But the server-side
                // refresh token may still be valid and replayable by
                // anything with access to where it was stored (XSS
                // payload, hostile extension), so warn the user — except
                // when the failure is just "tokens were already invalid"
                // (SESSION_ALREADY_ENDED). For interceptor-driven sign-
                // out that case stays silent; for user-clicked sign-out
                // we still want to inform them rather than silently mask
                // a remote revocation.
                console.error("Sign out error:", error);
                if (error instanceof AuthError && error.code === "SESSION_ALREADY_ENDED") {
                    sessionAlreadyEnded = true;
                } else {
                    serverSideSignOutFailed = true;
                }
            }

            this.$reset();

            // The notices below used to be shown after a soft route push. The
            // page is now discarded (see utils/session-teardown.ts for why a
            // push is not a sign-out), so they are handed across the reload
            // and replayed by App.vue on the login page instead.
            if (serverSideSignOutFailed) {
                stashPostSignOutNotice({
                    type: "error",
                    title: "Sign-out incomplete",
                    text: "We couldn't fully end your session on the server. Please close all browser windows for this site."
                });
            } else if (sessionAlreadyEnded && !viaInterceptor) {
                stashPostSignOutNotice({
                    type: "info",
                    title: "Session already ended",
                    text: "Your session had already ended (it may have been revoked elsewhere). You've been signed out."
                });
            }

            leaveToLogin();
        },

        // Leave a half-finished sign-in (mid-challenge "Back to log in").
        // Fire-and-forget: the provider's sign-out can hang on a
        // challenge-only session, and the caller tears the page down
        // regardless, so nothing waits on it and nothing to sign out of
        // is fine.
        abandonSignIn(): void {
            getAuthProvider().signOut().catch(() => { /* no-op: nothing to sign out of is fine */ });
        },

        async resetPassword(email: string) {
            await getAuthProvider().resetPassword(email);
        },

        async updateForgottenPassword({ email, code, newPassword }: IChangePassword) {
            await getAuthProvider().confirmResetPassword({
                email,
                code,
                newPassword
            });
        },

        hasPermissions(permissions: UserPermissions[]) {
            return permissions.every(perm => this.user?.permissions?.includes(perm));
        }
    }
});
