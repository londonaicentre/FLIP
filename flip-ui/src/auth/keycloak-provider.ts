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
 * Keycloak provider for the dev stack (FLIP#919): the OIDC Resource Owner
 * Password Credentials grant against the realm's token endpoint, so the user
 * keeps the in-app username/password form and nothing redirects.
 *
 * No SDK. Three `fetch` calls (password grant, refresh grant, logout), a JSON
 * blob in localStorage and an unverified decode of the ID token for the
 * display identity. Token verification is flip-api's job on every request.
 *
 * Deliberately capability-poor: ROPC cannot drive Keycloak's required actions
 * (password update, TOTP enrolment — those are browser flows in Keycloak's
 * own UI), and Keycloak answers a wrong password and a missing OTP with the
 * same `invalid_grant`, so an in-app TOTP step cannot be offered honestly.
 * The pages link out to the Keycloak console for those instead.
 */

import { decodeJwtPayload } from "./jwt";
import { AccountActionRequiredError,
    AuthCapabilities,
    AuthError,
    AuthProvider,
    AuthUser,
    SignInResult,
    SignInStep,
    TotpSetupDetails } from "./provider";

export type KeycloakConfig = {
    /** Public base URL of Keycloak as the browser reaches it, no trailing slash. */
    url: string;
    realm: string;
    clientId: string;
};

const STORAGE_KEY_PREFIX = "flip.auth.keycloak.";

// Refresh this far ahead of `expiresAt` so a request that leaves now does not
// arrive at flip-api with a token that expired in transit.
const REFRESH_SKEW_MS = 30_000;

type StoredSession = {
    accessToken: string;
    refreshToken: string;
    idToken: string;
    /** Epoch ms. */
    expiresAt: number;
    /** Epoch ms; 0 when Keycloak reported no refresh expiry. */
    refreshExpiresAt: number;
};

type TokenResponse = {
    access_token: string;
    refresh_token?: string;
    id_token?: string;
    expires_in: number;
    refresh_expires_in?: number;
};

type TokenError = {
    error?: string;
    error_description?: string;
};

/**
 * Read and validate the Keycloak values out of `js/window.js`. Throws with
 * every missing name so an operator fixes the env file in one go.
 */
export function readKeycloakConfig(): KeycloakConfig {
    const url = (window.KEYCLOAK_URL ?? "").trim().replace(/\/+$/, "");
    const realm = (window.KEYCLOAK_REALM ?? "").trim();
    const clientId = (window.KEYCLOAK_CLIENT_ID ?? "").trim();
    const missing = [
        !url && "KEYCLOAK_URL",
        !realm && "KEYCLOAK_REALM",
        !clientId && "KEYCLOAK_CLIENT_ID"
    ].filter((name): name is string => Boolean(name));
    if (missing.length > 0) {
        throw new Error(
            `AUTH_BACKEND is keycloak but ${missing.join(", ")} ${missing.length === 1 ? "is" : "are"} empty in ` +
            "js/window.js. Set KEYCLOAK_PUBLIC_URL, KEYCLOAK_REALM and KEYCLOAK_CLIENT_ID in the hub env file " +
            "and regenerate window.js (scripts/generate-window-js.sh)."
        );
    }

    return {
        url,
        realm,
        clientId
    };
}

function realmUrl(config: KeycloakConfig): string {
    return `${config.url}/realms/${encodeURIComponent(config.realm)}`;
}

/** Keycloak's self-service account console — where a user completes a required action. */
export function keycloakAccountUrl(config: KeycloakConfig = readKeycloakConfig()): string {
    return `${realmUrl(config)}/account`;
}

/** Keycloak's own "forgot password" page for this client. */
export function keycloakResetCredentialsUrl(config: KeycloakConfig = readKeycloakConfig()): string {
    return `${realmUrl(config)}/login-actions/reset-credentials?client_id=${encodeURIComponent(config.clientId)}`;
}

function isTokenResponse(body: unknown): body is TokenResponse {
    const b = body as TokenResponse | null;

    return typeof b?.access_token === "string" && typeof b?.expires_in === "number";
}

function unsupported(flow: string): AuthError {
    return new AuthError(
        "UNSUPPORTED",
        `${flow} is not available with the Keycloak backend — use the Keycloak console instead.`
    );
}

export class KeycloakAuthProvider implements AuthProvider {
    readonly backend = "keycloak" as const;

    readonly capabilities: AuthCapabilities = {
        newPasswordChallenge: false,
        totpChallenge: false,
        totpEnrolment: false,
        forgotPassword: false,
        adminResetPassword: false,
        globalSignOut: false
    };

    private config: KeycloakConfig | null = null;

    // One refresh at a time: a burst of parallel requests at token expiry
    // (every SWRV hook on a page) must produce one refresh grant, not one per
    // request — Keycloak rotates refresh tokens, so the second concurrent
    // refresh with the old token would be rejected and end the session.
    private refreshInFlight: Promise<string | null> | null = null;

    private readonly expiryListeners = new Set<() => void>();

    configure(): void {
        this.config = readKeycloakConfig();
    }

    private get cfg(): KeycloakConfig {
        if (this.config === null) {
            this.config = readKeycloakConfig();
        }

        return this.config;
    }

    private get storageKey(): string {
        return `${STORAGE_KEY_PREFIX}${this.cfg.clientId}`;
    }

    private get tokenEndpoint(): string {
        return `${realmUrl(this.cfg)}/protocol/openid-connect/token`;
    }

    private get logoutEndpoint(): string {
        return `${realmUrl(this.cfg)}/protocol/openid-connect/logout`;
    }

    // --- storage ---------------------------------------------------------

    private readSession(): StoredSession | null {
        try {
            const raw = window.localStorage.getItem(this.storageKey);
            if (raw === null) {
                return null;
            }
            const parsed = JSON.parse(raw) as Partial<StoredSession>;
            if (typeof parsed.accessToken !== "string" || typeof parsed.expiresAt !== "number") {
                return null;
            }

            return {
                accessToken: parsed.accessToken,
                refreshToken: typeof parsed.refreshToken === "string" ? parsed.refreshToken : "",
                idToken: typeof parsed.idToken === "string" ? parsed.idToken : "",
                expiresAt: parsed.expiresAt,
                refreshExpiresAt: typeof parsed.refreshExpiresAt === "number" ? parsed.refreshExpiresAt : 0
            };
        } catch {
            return null;
        }
    }

    private writeSession(session: StoredSession): void {
        window.localStorage.setItem(this.storageKey, JSON.stringify(session));
    }

    private clearSession(): void {
        window.localStorage.removeItem(this.storageKey);
    }

    private toSession(body: TokenResponse): StoredSession {
        const now = Date.now();

        return {
            accessToken: body.access_token,
            refreshToken: body.refresh_token ?? "",
            idToken: body.id_token ?? "",
            expiresAt: now + body.expires_in * 1000,
            refreshExpiresAt: body.refresh_expires_in ? now + body.refresh_expires_in * 1000 : 0
        };
    }

    // --- transport -------------------------------------------------------

    private async postForm(
        endpoint: string,
        params: Record<string, string>
    ): Promise<{ ok: boolean; status: number; body: unknown }> {
        const response = await fetch(endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: new URLSearchParams(params).toString()
        });
        let body: unknown = null;
        try {
            body = await response.json();
        } catch {
            // 204 from /logout, or a proxy error page: no JSON is fine.
            body = null;
        }

        return {
            ok: response.ok,
            status: response.status,
            body
        };
    }

    // --- sign-in ---------------------------------------------------------

    async signIn(username: string, password: string, opts?: { totp?: string }): Promise<SignInResult> {
        const params: Record<string, string> = {
            grant_type: "password",
            client_id: this.cfg.clientId,
            username,
            password,
            // `openid` so an ID token is issued — that is where `email` and
            // `preferred_username` for the display identity come from.
            scope: "openid"
        };
        if (opts?.totp) {
            params.totp = opts.totp;
        }

        const { ok, status, body } = await this.postForm(this.tokenEndpoint, params);
        if (!ok || !isTokenResponse(body)) {
            throw this.mapTokenError(body, status);
        }
        this.writeSession(this.toSession(body));

        return { step: SignInStep.DONE };
    }

    private mapTokenError(body: unknown, status: number): Error {
        const { error, error_description: description = "" } = (body ?? {}) as TokenError;
        if (error === "invalid_grant" && /not fully set up/i.test(description)) {
            // A required action (forced password change, incomplete profile,
            // TOTP enrolment) blocks the password grant until the user
            // completes it in Keycloak's own UI.
            return new AccountActionRequiredError(keycloakAccountUrl(this.cfg), description);
        }
        if (error === "invalid_grant" || error === "unauthorized_client" || error === "invalid_client") {
            return new AuthError("INVALID_CREDENTIALS", description || error);
        }

        return new Error(
            `Keycloak token request failed (HTTP ${status})${error ? `: ${error}` : ""}${description ? ` — ${description}` : ""}`
        );
    }

    async confirmNewPassword(): Promise<SignInResult> {
        throw unsupported("Changing a temporary password in-app");
    }

    async confirmTotpChallenge(): Promise<SignInResult> {
        throw unsupported("The in-app TOTP challenge");
    }

    async confirmTotpSetup(): Promise<SignInResult> {
        throw unsupported("In-app TOTP enrolment");
    }

    async setUpTotp(): Promise<TotpSetupDetails> {
        throw unsupported("In-app TOTP enrolment");
    }

    async verifyTotpSetup(): Promise<void> {
        throw unsupported("In-app TOTP enrolment");
    }

    // --- session ---------------------------------------------------------

    async getAccessToken(opts?: { forceRefresh?: boolean }): Promise<string | null> {
        const session = this.readSession();
        if (session === null) {
            return null;
        }
        const stale = Date.now() >= session.expiresAt - REFRESH_SKEW_MS;
        if (!stale && !opts?.forceRefresh) {
            return session.accessToken;
        }

        return this.refresh(session);
    }

    private refresh(session: StoredSession): Promise<string | null> {
        if (this.refreshInFlight !== null) {
            return this.refreshInFlight;
        }
        this.refreshInFlight = (async (): Promise<string | null> => {
            try {
                const { ok, body } = await this.postForm(this.tokenEndpoint, {
                    grant_type: "refresh_token",
                    client_id: this.cfg.clientId,
                    refresh_token: session.refreshToken
                });
                if (ok && isTokenResponse(body)) {
                    const next = this.toSession(body);
                    this.writeSession(next);

                    return next.accessToken;
                }
                if ((body as TokenError | null)?.error === "invalid_grant") {
                    // The refresh token was revoked or has expired: the session
                    // is over. Clear it so later calls answer null without a
                    // round-trip, and tell the app once.
                    this.clearSession();
                    this.fireSessionExpired();

                    return null;
                }
                console.warn("Keycloak token refresh failed:", body);
            } catch (e) {
                // Network blip / proxy error: not "session over". Keep the
                // stored tokens — the refresh token may well still be good.
                console.warn("Keycloak token refresh failed:", e);
            } finally {
                this.refreshInFlight = null;
            }

            // Fall back to the current token while it is still valid.
            return Date.now() < session.expiresAt ? session.accessToken : null;
        })();

        return this.refreshInFlight;
    }

    private fireSessionExpired(): void {
        for (const listener of this.expiryListeners) {
            listener();
        }
    }

    async getUser(): Promise<AuthUser> {
        const session = this.readSession();
        if (session === null) {
            throw new AuthError("MISSING_SESSION_TOKENS", "Not signed in to Keycloak.");
        }
        const claims = decodeJwtPayload(session.idToken || session.accessToken);
        const sub = typeof claims.sub === "string" ? claims.sub : "";
        if (!sub) {
            throw new Error("Keycloak token carries no `sub` claim.");
        }
        const email = typeof claims.email === "string" ? claims.email : undefined;
        const preferredUsername = typeof claims.preferred_username === "string" ? claims.preferred_username : undefined;

        return {
            sub,
            email: email ?? preferredUsername ?? "",
            username: preferredUsername ?? email ?? sub
        };
    }

    async hasSession(): Promise<boolean> {
        const session = this.readSession();
        if (session === null) {
            return false;
        }

        return session.refreshExpiresAt === 0 || Date.now() < session.refreshExpiresAt;
    }

    async signOut(): Promise<void> {
        // Read synchronously, before the first await: AuthLayout's "Back to
        // log in" clears localStorage right after calling us without waiting.
        const session = this.readSession();
        if (session === null) {
            return;
        }
        try {
            const { ok, status } = await this.postForm(this.logoutEndpoint, {
                client_id: this.cfg.clientId,
                refresh_token: session.refreshToken
            });
            if (!ok) {
                if (status === 400) {
                    // Keycloak rejects an already-invalid refresh token with 400
                    // invalid_grant — the session had ended before we asked.
                    throw new AuthError("SESSION_ALREADY_ENDED", "The Keycloak session had already ended.");
                }
                throw new Error(`Keycloak logout failed (HTTP ${status}).`);
            }
        } finally {
            // Local state goes regardless so the user can't keep using the app
            // from this tab; the store reports a server-side failure separately.
            this.clearSession();
        }
    }

    async resetPassword(): Promise<void> {
        throw unsupported("The in-app forgot-password flow");
    }

    async confirmResetPassword(): Promise<void> {
        throw unsupported("The in-app forgot-password flow");
    }

    onSessionExpired(cb: () => void): () => void {
        this.expiryListeners.add(cb);

        return () => {
            this.expiryListeners.delete(cb);
        };
    }
}
