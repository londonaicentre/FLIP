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
 * The provider-neutral authentication contract (FLIP#919).
 *
 * Everything above this seam — the Pinia auth store, the router guard, the
 * axios interceptor and the auth pages — speaks only these types. The two
 * implementations (`cognito-provider.ts` over aws-amplify for stag/prod,
 * `keycloak-provider.ts` over the OIDC password grant for the dev stack) are
 * selected once at boot from `window.AUTH_BACKEND` by `./index.ts`.
 *
 * This module is a LEAF: no imports, no side effects. Value exports here
 * (`SignInStep`, the error classes) can therefore be imported from production
 * code and specs alike without dragging a backend SDK in, and a spec that
 * mocks `@/auth` wholesale still gets the real classes from `@/auth/provider`.
 */

export type AuthBackend = "cognito" | "keycloak";

/**
 * Where a sign-in attempt stands. DONE means the provider's challenge chain
 * is cleared — it does NOT imply the app-gate will let the user through
 * (`mfaEnabled` in the store decides that).
 *
 * Neutral names on purpose: Cognito's own strings
 * (`CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED`, ...) are mapped to these by
 * the Cognito provider so no page has to know which backend is in play.
 */
export const SignInStep = {
    DONE: "DONE",
    NEW_PASSWORD_REQUIRED: "NEW_PASSWORD_REQUIRED", // pragma: allowlist secret
    TOTP_SETUP: "TOTP_SETUP",
    TOTP_CODE: "TOTP_CODE"
} as const;
export type SignInStep = (typeof SignInStep)[keyof typeof SignInStep];

export type AuthUser = {
    /** Stable subject identifier — the id flip-api keys users on. */
    sub: string;
    email: string;
    username: string;
};

// `setupUri` is an `otpauth://` URL for QR rendering; `sharedSecret` is the
// base32 secret users can type manually.
export type TotpSetupDetails = {
    sharedSecret: string;
    setupUri: string;
};

export type SignInResult =
    | { step: typeof SignInStep.DONE }
    | { step: typeof SignInStep.NEW_PASSWORD_REQUIRED }
    | { step: typeof SignInStep.TOTP_CODE }
    | { step: typeof SignInStep.TOTP_SETUP; totpSetup: TotpSetupDetails };

/**
 * What a backend can do. Pages guard on these rather than on the backend
 * name, so a capability a backend later gains needs no page change.
 */
export type AuthCapabilities = {
    /** First sign-in with a temporary password chains into a "choose a new password" step. */
    newPasswordChallenge: boolean;
    /** Returning users with TOTP active are challenged for a code in-app. */
    totpChallenge: boolean;
    /** The app can mint and verify a TOTP secret (QR enrolment page). */
    totpEnrolment: boolean;
    /** The in-app "Forgot password?" (code by email) flow works. */
    forgotPassword: boolean;
    /** An admin can trigger a password reset for another user from the users page. */
    adminResetPassword: boolean;
    /** Sign-out can revoke every session of the user server-side, not just this one. */
    globalSignOut: boolean;
};

export interface AuthProvider {
    readonly backend: AuthBackend;
    readonly capabilities: AuthCapabilities;

    /** One-time SDK / config initialisation, called from main.ts before the app mounts. */
    configure(): void;

    signIn(username: string, password: string, opts?: { totp?: string }): Promise<SignInResult>;
    confirmNewPassword(newPassword: string): Promise<SignInResult>;
    confirmTotpChallenge(code: string): Promise<SignInResult>;
    /** Sign-in-chain enrolment: verifies the code for the secret handed out with `TOTP_SETUP`. */
    confirmTotpSetup(code: string): Promise<SignInResult>;

    /** Post-auth enrolment: mint a fresh secret for an already signed-in user. */
    setUpTotp(accountLabel?: string): Promise<TotpSetupDetails>;
    /** Post-auth enrolment: verify the code for the secret from `setUpTotp` and activate TOTP. */
    verifyTotpSetup(code: string): Promise<void>;

    /** The bearer token for flip-api, or null when there is no usable session. Never throws. */
    getAccessToken(opts?: { forceRefresh?: boolean }): Promise<string | null>;
    getUser(): Promise<AuthUser>;
    /** True when a session with usable tokens exists — the router guard's question. Never throws. */
    hasSession(): Promise<boolean>;
    signOut(opts?: { global?: boolean }): Promise<void>;

    resetPassword(email: string): Promise<void>;
    confirmResetPassword(args: { email: string; code: string; newPassword: string }): Promise<void>;

    /** Subscribe to "the session can no longer be refreshed". Returns the unsubscribe function. */
    onSessionExpired(cb: () => void): () => void;
}

export type AuthErrorCode =
    | "INVALID_CREDENTIALS"
    | "SESSION_ALREADY_ENDED"
    | "ALREADY_SIGNED_IN"
    | "ACCOUNT_ACTION_REQUIRED"
    | "MISSING_SESSION_TOKENS"
    // TOTP was verified but the backend did not record the preference, so the
    // user is NOT MFA-enabled despite a clean code check. The store treats this
    // differently from a wrong code (secret consumed, state reset).
    | "MFA_PREFERENCE_FAILED"
    | "UNSUPPORTED";

/**
 * A failure the UI branches on. Backend SDK errors that the UI only ever
 * reports (wrong TOTP digits, network) are passed through untouched so the
 * pages' existing message heuristics keep working.
 */
export class AuthError extends Error {
    readonly code: AuthErrorCode;

    constructor(code: AuthErrorCode, message?: string, options?: { cause?: unknown }) {
        super(message ?? code, options);
        this.name = "AuthError";
        this.code = code;
    }
}

/**
 * The identity provider will not issue tokens until the user completes an
 * action in the provider's own UI (Keycloak "Account is not fully set up":
 * a required action such as a forced password update or a profile field).
 * The page shows `actionUrl` so the user can finish there and come back.
 */
export class AccountActionRequiredError extends AuthError {
    readonly actionUrl: string;

    constructor(actionUrl: string, message?: string) {
        super("ACCOUNT_ACTION_REQUIRED", message ?? "Finish setting up your account, then sign in again.");
        this.name = "AccountActionRequiredError";
        this.actionUrl = actionUrl;
    }
}
