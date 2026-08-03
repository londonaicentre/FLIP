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

import { fetchAuthSession } from "aws-amplify/auth";
import { Hub } from "aws-amplify/utils";
import { StoreGeneric } from "pinia";
import { NavigationGuardNext, RouteLocationNormalized } from "vue-router";

import router, { routeChange } from "@/router";
import { useAuthStore } from "@/store/auth";
import { useErrorStore } from "@/store/error";

import { Snackbar } from "./snackbar";

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
 * mount the page and trigger Amplify TOTP calls. Instead, the explicit
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
 * 2. clear localStorage (aws-amplify stores tokens here)
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

        // Public Ark+ demo build (VITE_DEMO): no Cognito, every route open.
        // Safe because the demo bundle talks only to the in-browser Mirage
        // server (mocks/demo-server.ts), which has no passthrough — there is
        // no real backend to protect. Vite inlines the flag, so no other
        // build ships this branch.
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
            if (auth.signInStep === "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED") {
                return to.path === "/auth/new-password" ? next() : next("/auth/new-password");
            }
            if (auth.signInStep === "CONTINUE_SIGN_IN_WITH_TOTP_SETUP") {
                return to.path === "/auth/mfa-setup" ? next() : next("/auth/mfa-setup");
            }
            if (auth.signInStep === "CONFIRM_SIGN_IN_WITH_TOTP_CODE") {
                return to.path === "/auth/mfa-verify" ? next() : next("/auth/mfa-verify");
            }

            const stored = window.localStorage.getItem("cypress.auth.user");
            if (stored && !auth.user) {
                try {
                    auth.user = JSON.parse(stored);
                    auth.signInStep = "DONE";
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

        // Check if user has a valid session
        try {
            await fetchAuthSession();
        } catch {
            // No valid session, redirect to login
            auth.user = null;
            auth.signInStep = null;

            return next("/auth/login");
        }

        // If we are in new-password challenge
        if (auth.signInStep === "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED") {
            return next("/auth/new-password");
        }

        // First-time MFA enrollment (TOTP setup with QR code), sign-in chain.
        // Same-path guard: if the user is already on /auth/mfa-setup let
        // them stay there — re-issuing `next('/auth/mfa-setup')` would
        // raise NavigationDuplicated in vue-router.
        if (auth.signInStep === "CONTINUE_SIGN_IN_WITH_TOTP_SETUP") {
            return to.path === "/auth/mfa-setup" ? next() : next("/auth/mfa-setup");
        }

        // Returning user — enter the code from their authenticator app.
        if (auth.signInStep === "CONFIRM_SIGN_IN_WITH_TOTP_CODE") {
            return to.path === "/auth/mfa-verify" ? next() : next("/auth/mfa-verify");
        }

        // Load user info if needed (page reload after sign-in). This also
        // populates `mfaEnabled` from the backend so the MFA-gate check
        // below has a definitive answer to work with.
        if (!auth.user || auth.mfaEnabled === null) {
            await auth.fetchInfo();
        }

        // Admin-reset users (or anyone Cognito signed in without MFA)
        // can only reach the enrolment page until they finish setup —
        // but only if this environment requires MFA at all. In dev
        // (backend Settings.ENFORCE_MFA=False), `mfaRequired` is false
        // and we skip the redirect even for unenrolled users.
        if (auth.mfaRequired === true && auth.mfaEnabled === false && to.path !== "/auth/mfa-setup") {
            return next("/auth/mfa-setup");
        }

        return next();

    } catch {
        auth.$reset();
        localStorage.clear();
        routeChange.gotoLogin();
        Snackbar.error({
            title: "You've been signed out",
            text: "Please log in again to confirm your identity."
        });
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
    return authStore.signInStep === "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED";
};

export const apiGateway = "CentralHubAPIGateway";

// Read Cognito config at runtime from window.* (populated by
// public/js/window.js in dev and dist/js/window.js in prod — both
// loaded synchronously before main.ts). The generator reads from
// AWS_COGNITO_USER_POOL_ID / AWS_COGNITO_APP_CLIENT_ID / AWS_REGION,
// matching the rest of the stack; no VITE_AWS_* duplication.
export const authConfig = {
    Auth: {
        Cognito: {
            region: window.AWS_REGION || "eu-west-2",
            userPoolId: window.AWS_USER_POOL_ID,
            userPoolClientId: window.AWS_CLIENT_ID
        }
    }
};

let tokenRefreshTimeout = 0;

// Routes where a tokenRefresh_failure or background 401 must NOT force
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

const listener = (data: { payload: { event: string } }) => {
    const store = useAuthStore();

    switch (data.payload.event) {
        case "tokenRefresh_failure":
            // Compare against path (no query/fragment) so a whitelisted
            // route with a `?redirect=...` or `#frag` still skips the
            // forced sign-out. `fullPath` would miss the membership test.
            if (NO_FORCED_SIGNOUT_PATHS.has(router.currentRoute.value.path)) {
                break;
            }

            if (tokenRefreshTimeout) {
                clearTimeout(tokenRefreshTimeout);
                tokenRefreshTimeout = 0;
            }

            tokenRefreshTimeout = window.setTimeout(() => {
                Snackbar.error({
                    title: "You've been signed out",
                    text: "Your session has expired. Please log in again."
                }, 60_000);

                routeChange.gotoLogin();
                store.$reset();
            }, 100);
            break;
    }
};

Hub.listen("auth", listener);

// Cypress test hook: lets specs trigger the same Hub-driven session-expiry
// codepath production hits when Amplify's refresh fails. Doing it as a
// window-mounted dispatcher (vs poking the SDK from the spec) keeps the
// test surface narrow — specs only see the user-visible behaviour, not
// the internal Hub event names.
//
// Build-time gate so the dispatcher never ships in production. A runtime
// `window.Cypress` check would let any browser extension or injected
// script enable this hook and force-sign-out a real user.
if (typeof window !== "undefined" && isCypressMode()) {
    (window as unknown as { __cypressTriggerSessionExpiry?: () => void })
        .__cypressTriggerSessionExpiry = () => {
            Hub.dispatch("auth", { event: "tokenRefresh_failure" });
        };
}
