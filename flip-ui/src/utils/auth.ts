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

import { StoreGeneric } from "pinia";
import { NavigationGuardNext, RouteLocationNormalized } from "vue-router";

import { getAuthProvider } from "@/auth";
import { SignInStep } from "@/auth/provider";
import router from "@/router";
import { useAuthStore } from "@/store/auth";
import { useErrorStore } from "@/store/error";

import { leaveToLogin, stashPostSignOutNotice } from "./session-teardown";

/**
 * True when the bundle was built with the Cypress E2E flag set
 * (VITE_E2E=true). Vite inlines this at build time and dead-code-
 * eliminates the surrounding branches in any other build, so the
 * test seams below cannot be enabled from a production bundle —
 * even by an attacker who flips `window.Cypress` in dev tools.
 */
function isCypressMode(): boolean {
    return import.meta.env.VITE_E2E === "true";
}

/**
 * By default all routes are guarded and authentication is required.
 * This serves as an allowed list of routes which will bypass the auth check.
 *
 * MFA pages (/auth/mfa-setup, /auth/mfa-verify) are intentionally NOT
 * unguarded: they must be reachable only mid-challenge or post-auth-with-
 * MFA-required. Listing them here would let an unauthenticated visitor
 * mount the page and trigger provider TOTP calls. Instead, the explicit
 * signInStep / mfa-required redirects below route legitimate users to
 * these pages and a same-path guard prevents recursive redirects.
 * @type {string[]}
 */
const unguardedRoutes: string[] = [
    "/auth/login",
    "/auth/new-password",
    "/auth/change-password",
    "/auth/access-request",
    "/privacy-policy",
    "/terms-of-service"
];

/**
 * Checks auth status for the requested route.
 * Bypasses check for any routes that are included in the unguarded routes list.
 * On any error getting the authenticated user:
 * 1. clear down any existing auth state
 * 2. clear localStorage (both providers keep their tokens here)
 * 3. Redirect to login page
 * 4. Give the user some indication as to what's going on
 */
export const authCheck = async (
    to: RouteLocationNormalized,
    _from: RouteLocationNormalized,
    next: NavigationGuardNext
): Promise<void> => {
    const errorStore = useErrorStore();
    const auth = useAuthStore();

    try {
        errorStore.clearError();

        if (unguardedRoutes.includes(to.path)) {
            return next();
        }

        if (import.meta.env.VITE_LOCAL === "true") {
            return next();
        }

        // Public Ark+ demo build (VITE_DEMO): no identity provider, every
        // route open. Safe because the demo bundle talks only to the
        // in-browser Mirage server (mocks/demo-server.ts), which has no
        // passthrough — there is no real backend to protect. Vite inlines
        // the flag, so no other build ships this branch.
        if (import.meta.env.VITE_DEMO === "true") {
            return next();
        }

        // Cypress E2E test seam. Gated on a build-time mode flag rather than
        // a runtime window probe so a stray script setting `window.Cypress`
        // can never enable it in a production bundle.
        // Tests can't simulate Cognito's SRP handshake against a static
        // fixture; the `cypress.auth.user` localStorage key is the
        // documented contract (see test/cypress/support/cognito.ts).
        if (isCypressMode()) {
            if (auth.signInStep === SignInStep.NEW_PASSWORD_REQUIRED) {
                return to.path === "/auth/new-password" ? next() : next("/auth/new-password");
            }
            if (auth.signInStep === SignInStep.TOTP_SETUP) {
                return to.path === "/auth/mfa-setup" ? next() : next("/auth/mfa-setup");
            }
            if (auth.signInStep === SignInStep.TOTP_CODE) {
                return to.path === "/auth/mfa-verify" ? next() : next("/auth/mfa-verify");
            }

            const stored = window.localStorage.getItem("cypress.auth.user");
            if (stored && !auth.user) {
                try {
                    auth.user = JSON.parse(stored);
                    auth.signInStep = SignInStep.DONE;
                } catch (e) {
                    // Don't fall through to the umbrella catch — that path
                    // would clear localStorage and show a generic
                    // "signed out" snackbar, hiding the real cause from a
                    // confused test author. Treat malformed JSON as
                    // "no fixture present" and let the redirect below run.
                    console.error("Cypress auth fixture is malformed JSON:", e);
                    window.localStorage.removeItem("cypress.auth.user");
                }
            }
            if (!auth.user) {
                return next("/auth/login");
            }

            return next();
        }

        // Mid-challenge users have no session yet — route them to the page
        // that finishes their chain BEFORE asking the provider for one.

        // If we are in new-password challenge
        if (auth.signInStep === SignInStep.NEW_PASSWORD_REQUIRED) {
            return next("/auth/new-password");
        }

        // First-time MFA enrollment (TOTP setup with QR code), sign-in chain.
        // Same-path guard: if the user is already on /auth/mfa-setup let
        // them stay there — re-issuing `next('/auth/mfa-setup')` would
        // raise NavigationDuplicated in vue-router.
        if (auth.signInStep === SignInStep.TOTP_SETUP) {
            return to.path === "/auth/mfa-setup" ? next() : next("/auth/mfa-setup");
        }

        // Returning user — enter the code from their authenticator app.
        if (auth.signInStep === SignInStep.TOTP_CODE) {
            return to.path === "/auth/mfa-verify" ? next() : next("/auth/mfa-verify");
        }

        // Check if user has a valid session
        if (!(await auth.hasSession())) {
            // No valid session, redirect to login
            auth.user = null;
            auth.signInStep = null;

            return next("/auth/login");
        }

        // Load user info if needed (page reload after sign-in). This also
        // populates `mfaEnabled` from the backend so the MFA-gate check
        // below has a definitive answer to work with.
        if (!auth.user || auth.mfaEnabled === null) {
            await auth.fetchInfo();
        }

        // Admin-reset users (or anyone the provider signed in without MFA)
        // can only reach the enrolment page until they finish setup —
        // but only if this environment requires MFA at all, and only
        // where the backend lets the app run the enrolment. In dev
        // (backend Settings.ENFORCE_MFA=False), `mfaRequired` is false
        // and we skip the redirect even for unenrolled users; with a
        // backend that cannot enrol TOTP in-app (Keycloak) the page
        // would have nothing to offer.
        if (
            auth.mfaRequired === true
            && auth.mfaEnabled === false
            && auth.capabilities.totpEnrolment
            && to.path !== "/auth/mfa-setup"
        ) {
            return next("/auth/mfa-setup");
        }

        return next();

    } catch {
        auth.$reset();
        localStorage.clear();
        // Same teardown as signOut (utils/session-teardown.ts): a route push
        // would leave every cache and store of the ended session in memory.
        // The notice rides across the reload in sessionStorage, which the
        // localStorage.clear() above does not touch.
        stashPostSignOutNotice({
            type: "error",
            title: "You've been signed out",
            text: "Please log in again to confirm your identity."
        });
        leaveToLogin();
    }
};

export const isUserUnconfirmedCheck = async (
    authStore: StoreGeneric
): Promise<boolean> => {
    // True only when the caller is genuinely in the new-password
    // challenge step this page handles. Everything else — fully signed
    // in, mid-TOTP, signed out, or a challenge we don't own — should be
    // redirected away by the caller (usually to viewProjects, where the
    // router guard sorts them out).
    return authStore.signInStep === SignInStep.NEW_PASSWORD_REQUIRED;
};

export const apiGateway = "CentralHubAPIGateway";

let tokenRefreshTimeout = 0;

// Routes where a session-expiry signal or background 401 must NOT force
// the user to log in. Two classes of page:
//   1. No session exists yet — login, new-password (pre-auth challenge),
//      change-password (forgot-password flow), access-request. Amplify
//      emits `tokenRefresh_failure` periodically on these because there
//      are no tokens to refresh; forwarding that to gotoLogin would
//      interrupt the user mid-form (e.g. filling in a reset code).
//   2. Mid-challenge flows — mfa-setup, mfa-verify. A yank to login
//      would lose challenge state or interrupt TOTP enrolment.
export const NO_FORCED_SIGNOUT_PATHS = new Set<string>([
    "/auth/login",
    "/auth/new-password",
    "/auth/change-password",
    "/auth/access-request",
    "/auth/mfa-setup",
    "/auth/mfa-verify"
]);

/**
 * The session can no longer be refreshed (Cognito rejected the refresh
 * token; Keycloak answered the refresh grant with invalid_grant). Tear the
 * session down and land on the login page with a notice. Registered with
 * the provider by main.ts (`onSessionExpired`).
 */
export const handleSessionExpired = (): void => {
    const store = useAuthStore();

    // Compare against path (no query/fragment) so a whitelisted
    // route with a `?redirect=...` or `#frag` still skips the
    // forced sign-out. `fullPath` would miss the membership test.
    if (NO_FORCED_SIGNOUT_PATHS.has(router.currentRoute.value.path)) {
        return;
    }

    if (tokenRefreshTimeout) {
        clearTimeout(tokenRefreshTimeout);
        tokenRefreshTimeout = 0;
    }

    tokenRefreshTimeout = window.setTimeout(() => {
        // Fourth and last session-ending path; torn down like the
        // others (utils/session-teardown.ts), notice replayed on boot.
        stashPostSignOutNotice({
            type: "error",
            title: "You've been signed out",
            text: "Your session has expired. Please log in again."
        });
        store.$reset();
        leaveToLogin();
    }, 100);
};

// Cypress test hooks.
//
// `__cypressTriggerSessionExpiry` lets specs trigger the same session-expiry
// codepath production hits when the provider's refresh fails. Doing it as a
// window-mounted dispatcher (vs poking the SDK from the spec) keeps the test
// surface narrow — specs only see the user-visible behaviour, not the
// provider's internal event names.
//
// `__cypressGetAuthUser` hands the demo recorder (test/cypress/demo) the
// live session in provider-neutral form after a REAL sign-in, so it can
// satisfy the `cypress.auth.user` contract without knowing where a given
// backend caches its tokens.
//
// Build-time gate so neither hook ever ships in production. A runtime
// `window.Cypress` check would let any browser extension or injected
// script enable them and force-sign-out a real user or read their token.
if (typeof window !== "undefined" && isCypressMode()) {
    (window as unknown as {
        __cypressTriggerSessionExpiry?: () => void;
        __cypressGetAuthUser?: () => Promise<{ token: string | null; user: unknown }>;
    }).__cypressTriggerSessionExpiry = () => {
        handleSessionExpired();
    };
    (window as unknown as {
        __cypressGetAuthUser?: () => Promise<{ token: string | null; user: unknown }>;
    }).__cypressGetAuthUser = async () => {
        const provider = getAuthProvider();
        const token = await provider.getAccessToken();

        return {
            token,
            user: token === null ? null : await provider.getUser()
        };
    };
}
