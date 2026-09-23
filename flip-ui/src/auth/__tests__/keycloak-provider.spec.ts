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

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {     keycloakAccountUrl,
    KeycloakAuthProvider,
    keycloakResetCredentialsUrl,
    readKeycloakConfig } from "@/auth/keycloak-provider";
import { AccountActionRequiredError, AuthError, type AuthProvider, SignInStep } from "@/auth/provider";

const KC_URL = "http://keycloak.test:8081";
const REALM = "flip";
const CLIENT_ID = "flip-ui";
const TOKEN_ENDPOINT = `${KC_URL}/realms/${REALM}/protocol/openid-connect/token`;
const LOGOUT_ENDPOINT = `${KC_URL}/realms/${REALM}/protocol/openid-connect/logout`;
const STORAGE_KEY = `flip.auth.keycloak.${CLIENT_ID}`;

// env.d.ts declares the window.KEYCLOAK_* keys as required strings; the
// specs need them absent, hence the untyped delete.
const win = window as unknown as Record<string, unknown>;

const originalFetch = global.fetch;
const fetchMock = vi.fn();

function jsonResponse(status: number, body: unknown): Response {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => body
    } as unknown as Response;
}

/** A response with no JSON body: Keycloak's 204 from /logout, or a proxy's HTML error page. */
function emptyResponse(status: number): Response {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => {
            throw new SyntaxError("Unexpected end of JSON input");
        }
    } as unknown as Response;
}

function makeJwt(payload: Record<string, unknown>): string {
    const b64url = (s: string): string =>
        Buffer.from(s, "utf8").toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

    return `${b64url(JSON.stringify({ alg: "RS256" }))}.${b64url(JSON.stringify(payload))}.sig`;
}

const ID_TOKEN = makeJwt({
    sub: "kc-sub-1",
    email: "alice@example.com",
    preferred_username: "alice"
});
const ACCESS_TOKEN = makeJwt({
    sub: "kc-sub-1",
    preferred_username: "alice"
});

function tokenResponse(overrides: Record<string, unknown> = {}): Record<string, unknown> {
    return {
        access_token: ACCESS_TOKEN,
        refresh_token: "refresh-1",
        id_token: ID_TOKEN,
        token_type: "Bearer",
        expires_in: 300,
        refresh_expires_in: 1800,
        ...overrides
    };
}

/** The form body of the n-th fetch call, decoded. */
function sentForm(callIndex = 0): URLSearchParams {
    const [, init] = fetchMock.mock.calls[callIndex] as [string, RequestInit];

    return new URLSearchParams(init.body as string);
}

function storedSession(): Record<string, unknown> | null {
    const raw = window.localStorage.getItem(STORAGE_KEY);

    return raw ? JSON.parse(raw) : null;
}

function seedSession(overrides: Record<string, unknown> = {}): void {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({
        accessToken: ACCESS_TOKEN,
        refreshToken: "refresh-1",
        idToken: ID_TOKEN,
        expiresAt: Date.now() + 300_000,
        refreshExpiresAt: Date.now() + 1_800_000,
        ...overrides
    }));
}

describe("KeycloakAuthProvider", () => {
    let provider: KeycloakAuthProvider;

    beforeEach(() => {
        win.KEYCLOAK_URL = KC_URL;
        win.KEYCLOAK_REALM = REALM;
        win.KEYCLOAK_CLIENT_ID = CLIENT_ID;
        window.localStorage.clear();
        fetchMock.mockReset();
        global.fetch = fetchMock as unknown as typeof fetch;
        provider = new KeycloakAuthProvider();
    });

    afterEach(() => {
        global.fetch = originalFetch;
        window.localStorage.clear();
        vi.useRealTimers();
        delete win.KEYCLOAK_URL;
        delete win.KEYCLOAK_REALM;
        delete win.KEYCLOAK_CLIENT_ID;
    });

    describe("identity and capabilities", () => {
        it("identifies as the keycloak backend", () => {
            expect(provider.backend).toBe("keycloak");
        });

        it("offers no Cognito-only flows: ROPC cannot enrol TOTP, and Keycloak's invalid_grant hides which factor failed", () => {
            expect(provider.capabilities).toEqual({
                newPasswordChallenge: false,
                totpChallenge: false,
                totpEnrolment: false,
                forgotPassword: false,
                adminResetPassword: false,
                globalSignOut: false
            });
        });
    });

    describe("configuration", () => {
        it("configure() validates the three window values and names every missing one", () => {
            win.KEYCLOAK_URL = "";
            win.KEYCLOAK_CLIENT_ID = "   ";

            // Names exactly the empty ones (REALM is set), then says where
            // the values come from.
            expect(() => provider.configure()).toThrow(/but KEYCLOAK_URL, KEYCLOAK_CLIENT_ID are empty/);
            expect(() => provider.configure()).toThrow(/window\.js/);
        });

        it("configure() passes when all three are set, and strips a trailing slash from the URL", () => {
            win.KEYCLOAK_URL = `${KC_URL}/`;

            expect(() => provider.configure()).not.toThrow();
            expect(readKeycloakConfig().url).toBe(KC_URL);
        });

        it("builds the account and reset-credentials URLs from the same config", () => {
            expect(keycloakAccountUrl()).toBe(`${KC_URL}/realms/${REALM}/account`);
            expect(keycloakResetCredentialsUrl()).toBe(
                `${KC_URL}/realms/${REALM}/login-actions/reset-credentials?client_id=${CLIENT_ID}`
            );
        });
    });

    describe("signIn (Resource Owner Password grant)", () => {
        it("POSTs the password grant as a form with scope=openid and resolves DONE", async () => {
            fetchMock.mockResolvedValueOnce(jsonResponse(200, tokenResponse()));

            const result = await provider.signIn("alice", "s3cret");  // pragma: allowlist secret

            expect(result).toEqual({ step: SignInStep.DONE });
            expect(fetchMock).toHaveBeenCalledTimes(1);
            const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
            expect(url).toBe(TOKEN_ENDPOINT);
            expect(init.method).toBe("POST");
            expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/x-www-form-urlencoded");
            const form = sentForm();
            expect(form.get("grant_type")).toBe("password");
            expect(form.get("client_id")).toBe(CLIENT_ID);
            expect(form.get("username")).toBe("alice");
            expect(form.get("password")).toBe("s3cret");
            expect(form.get("scope")).toBe("openid");
            expect(form.has("totp")).toBe(false);
        });

        it("forwards a TOTP code as the `totp` form field when given", async () => {
            fetchMock.mockResolvedValueOnce(jsonResponse(200, tokenResponse()));

            await provider.signIn("alice", "pw", { totp: "123456" });

            expect(sentForm().get("totp")).toBe("123456");
        });

        it("persists the token set under flip.auth.keycloak.<clientId> with absolute expiry times", async () => {
            vi.useFakeTimers();
            vi.setSystemTime(new Date("2026-09-23T10:00:00Z"));
            fetchMock.mockResolvedValueOnce(jsonResponse(200, tokenResponse()));

            await provider.signIn("alice", "pw");

            expect(storedSession()).toEqual({
                accessToken: ACCESS_TOKEN,
                refreshToken: "refresh-1",
                idToken: ID_TOKEN,
                expiresAt: Date.now() + 300_000,
                refreshExpiresAt: Date.now() + 1_800_000
            });
        });

        it("maps invalid_grant 'Account is not fully set up' to AccountActionRequiredError pointing at the account console", async () => {
            fetchMock.mockResolvedValueOnce(jsonResponse(400, {
                error: "invalid_grant",
                error_description: "Account is not fully set up"
            }));

            const err = await provider.signIn("alice", "pw").catch((e: unknown) => e);

            expect(err).toBeInstanceOf(AccountActionRequiredError);
            expect((err as AccountActionRequiredError).code).toBe("ACCOUNT_ACTION_REQUIRED");
            expect((err as AccountActionRequiredError).actionUrl).toBe(`${KC_URL}/realms/${REALM}/account`);
            expect(storedSession()).toBeNull();
        });

        it.each([
            ["invalid_grant", "Invalid user credentials"],
            ["unauthorized_client", "Client not allowed for direct access grants"],
            ["invalid_client", "Invalid client or Invalid client credentials"]
        ])("maps %s to AuthError INVALID_CREDENTIALS", async (error, error_description) => {
            fetchMock.mockResolvedValueOnce(jsonResponse(401, {
                error,
                error_description
            }));

            const err = await provider.signIn("alice", "wrong").catch((e: unknown) => e);

            expect(err).toBeInstanceOf(AuthError);
            expect((err as AuthError).code).toBe("INVALID_CREDENTIALS");
            expect(storedSession()).toBeNull();
        });

        it("surfaces any other failure as a plain error naming the status", async () => {
            fetchMock.mockResolvedValueOnce(jsonResponse(503, null));

            const err = await provider.signIn("alice", "pw").catch((e: unknown) => e);

            expect(err).toBeInstanceOf(Error);
            expect(err).not.toBeInstanceOf(AuthError);
            expect((err as Error).message).toMatch(/503/);
        });
    });

    describe("getAccessToken", () => {
        it("returns null without a network call when nothing is stored", async () => {
            await expect(provider.getAccessToken()).resolves.toBeNull();
            expect(fetchMock).not.toHaveBeenCalled();
        });

        it("returns the stored token without a network call while it is fresh", async () => {
            seedSession();

            await expect(provider.getAccessToken()).resolves.toBe(ACCESS_TOKEN);
            expect(fetchMock).not.toHaveBeenCalled();
        });

        it("refreshes when the token is within 30 s of expiry", async () => {
            vi.useFakeTimers();
            vi.setSystemTime(new Date("2026-09-23T10:00:00Z"));
            seedSession({ expiresAt: Date.now() + 20_000 });
            const fresh = makeJwt({ sub: "kc-sub-1" });
            fetchMock.mockResolvedValueOnce(jsonResponse(200, tokenResponse({
                access_token: fresh,
                refresh_token: "refresh-2"
            })));

            await expect(provider.getAccessToken()).resolves.toBe(fresh);

            expect(fetchMock).toHaveBeenCalledWith(TOKEN_ENDPOINT, expect.objectContaining({ method: "POST" }));
            const form = sentForm();
            expect(form.get("grant_type")).toBe("refresh_token");
            expect(form.get("client_id")).toBe(CLIENT_ID);
            expect(form.get("refresh_token")).toBe("refresh-1");
            // Rotated refresh token and new expiry are persisted.
            expect(storedSession()).toMatchObject({
                accessToken: fresh,
                refreshToken: "refresh-2",
                expiresAt: Date.now() + 300_000
            });
        });

        it("refreshes on forceRefresh even when the token is fresh", async () => {
            seedSession();
            const fresh = makeJwt({ sub: "kc-sub-1" });
            fetchMock.mockResolvedValueOnce(jsonResponse(200, tokenResponse({ access_token: fresh })));

            await expect(provider.getAccessToken({ forceRefresh: true })).resolves.toBe(fresh);
            expect(sentForm().get("grant_type")).toBe("refresh_token");
        });

        it("coalesces concurrent refreshes behind one in-flight request", async () => {
            seedSession({ expiresAt: Date.now() + 1_000 });
            const fresh = makeJwt({ sub: "kc-sub-1" });
            let resolveFetch!: (r: Response) => void;
            fetchMock.mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveFetch = resolve; }));

            const a = provider.getAccessToken();
            const b = provider.getAccessToken();
            const c = provider.getAccessToken({ forceRefresh: true });
            resolveFetch(jsonResponse(200, tokenResponse({ access_token: fresh })));

            await expect(Promise.all([a, b, c])).resolves.toEqual([fresh, fresh, fresh]);
            expect(fetchMock).toHaveBeenCalledTimes(1);
        });

        it("on invalid_grant: clears storage, fires onSessionExpired listeners once, returns null", async () => {
            seedSession({ expiresAt: Date.now() + 1_000 });
            const listener = vi.fn();
            const other = vi.fn();
            provider.onSessionExpired(listener);
            const unsubscribe = provider.onSessionExpired(other);
            unsubscribe();
            fetchMock.mockResolvedValueOnce(jsonResponse(400, {
                error: "invalid_grant",
                error_description: "Token is not active"
            }));

            await expect(provider.getAccessToken()).resolves.toBeNull();

            expect(storedSession()).toBeNull();
            expect(listener).toHaveBeenCalledTimes(1);
            expect(other).not.toHaveBeenCalled();

            // The session is gone: later calls are null without another
            // round-trip and without re-firing the listeners.
            await expect(provider.getAccessToken()).resolves.toBeNull();
            expect(fetchMock).toHaveBeenCalledTimes(1);
            expect(listener).toHaveBeenCalledTimes(1);
        });

        it("keeps the still-valid token when the refresh fails for a non-auth reason", async () => {
            // A 502 from a proxy or a network blip is not "session over": the
            // refresh token may well still be good, so nothing is cleared and
            // the caller gets the token that has not expired yet.
            const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
            seedSession({ expiresAt: Date.now() + 10_000 });
            const listener = vi.fn();
            provider.onSessionExpired(listener);
            fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

            await expect(provider.getAccessToken()).resolves.toBe(ACCESS_TOKEN);

            expect(storedSession()).not.toBeNull();
            expect(listener).not.toHaveBeenCalled();
            expect(consoleWarn).toHaveBeenCalledWith("Keycloak token refresh failed:", expect.any(Error));
            consoleWarn.mockRestore();
        });

        it("returns null (without clearing) when the refresh fails for a non-auth reason and the token has already expired", async () => {
            const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
            seedSession({ expiresAt: Date.now() - 1_000 });
            fetchMock.mockResolvedValueOnce(jsonResponse(502, null));

            await expect(provider.getAccessToken()).resolves.toBeNull();

            expect(storedSession()).not.toBeNull();
            consoleWarn.mockRestore();
        });
    });

    describe("getUser", () => {
        it("decodes sub, email and preferred_username from the ID token", async () => {
            seedSession();

            await expect(provider.getUser()).resolves.toEqual({
                sub: "kc-sub-1",
                email: "alice@example.com",
                username: "alice"
            });
        });

        it("falls back to preferred_username for email, and to email for username, when a claim is absent", async () => {
            seedSession({
                idToken: makeJwt({
                    sub: "s",
                    preferred_username: "bob"
                })
            });
            await expect(provider.getUser()).resolves.toEqual({
                sub: "s",
                email: "bob",
                username: "bob"
            });

            seedSession({
                idToken: makeJwt({
                    sub: "s",
                    email: "carol@example.com"
                })
            });
            await expect(provider.getUser()).resolves.toEqual({
                sub: "s",
                email: "carol@example.com",
                username: "carol@example.com"
            });
        });

        it("falls back to the access token when no ID token was issued", async () => {
            seedSession({ idToken: "" });

            await expect(provider.getUser()).resolves.toEqual({
                sub: "kc-sub-1",
                email: "alice",
                username: "alice"
            });
        });

        it("throws AuthError MISSING_SESSION_TOKENS when signed out", async () => {
            const err = await provider.getUser().catch((e: unknown) => e);

            expect(err).toBeInstanceOf(AuthError);
            expect((err as AuthError).code).toBe("MISSING_SESSION_TOKENS");
        });

        it("rejects a token that carries no `sub` claim rather than inventing an identity", async () => {
            // `sub` is the user id the store keys permissions on; a token
            // without one must not produce a user with an empty id.
            seedSession({ idToken: makeJwt({ preferred_username: "nobody" }) });

            await expect(provider.getUser()).rejects.toThrow(/no `sub` claim/);
        });
    });

    describe("hasSession", () => {
        it("is false with nothing stored and false for an unparseable blob", async () => {
            await expect(provider.hasSession()).resolves.toBe(false);

            window.localStorage.setItem(STORAGE_KEY, "{not json");
            await expect(provider.hasSession()).resolves.toBe(false);
        });

        it("is false for a blob that parses but lacks the token fields, and no token is handed out from it", async () => {
            // An older or corrupted session shape (say, a tampered
            // `expiresAt`) is treated as signed out rather than trusted.
            window.localStorage.setItem(STORAGE_KEY, JSON.stringify({
                accessToken: ACCESS_TOKEN,
                expiresAt: "never"
            }));

            await expect(provider.hasSession()).resolves.toBe(false);
            await expect(provider.getAccessToken()).resolves.toBeNull();
            expect(fetchMock).not.toHaveBeenCalled();
        });

        it("is true while the refresh token has not expired", async () => {
            seedSession();

            await expect(provider.hasSession()).resolves.toBe(true);
        });

        it("is false once the refresh token has expired", async () => {
            seedSession({ refreshExpiresAt: Date.now() - 1 });

            await expect(provider.hasSession()).resolves.toBe(false);
        });

        it("is true when Keycloak reported no refresh expiry (refresh_expires_in = 0)", async () => {
            seedSession({ refreshExpiresAt: 0 });

            await expect(provider.hasSession()).resolves.toBe(true);
        });
    });

    describe("signOut", () => {
        it("POSTs client_id + refresh_token to the logout endpoint and clears storage", async () => {
            seedSession();
            // Keycloak answers /logout with a body-less 204, which `json()`
            // rejects on; that is success, not a failure.
            fetchMock.mockResolvedValueOnce(emptyResponse(204));

            // `global` is meaningless here (no server-side session list to
            // revoke), so the store's `{ global: false }` is ignored.
            await expect(provider.signOut()).resolves.toBeUndefined();

            const [url] = fetchMock.mock.calls[0] as [string];
            expect(url).toBe(LOGOUT_ENDPOINT);
            const form = sentForm();
            expect(form.get("client_id")).toBe(CLIENT_ID);
            expect(form.get("refresh_token")).toBe("refresh-1");
            expect(storedSession()).toBeNull();
        });

        it("maps a 400 (refresh token already invalid) to AuthError SESSION_ALREADY_ENDED and still clears storage", async () => {
            seedSession();
            fetchMock.mockResolvedValueOnce(jsonResponse(400, {
                error: "invalid_grant",
                error_description: "Invalid refresh token"
            }));

            const err = await provider.signOut().catch((e: unknown) => e);

            expect(err).toBeInstanceOf(AuthError);
            expect((err as AuthError).code).toBe("SESSION_ALREADY_ENDED");
            expect(storedSession()).toBeNull();
        });

        it("rethrows other failures but clears storage regardless", async () => {
            seedSession();
            fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

            await expect(provider.signOut()).rejects.toThrow("Failed to fetch");
            expect(storedSession()).toBeNull();
        });

        it("surfaces a non-400 HTTP failure as a plain error naming the status, and clears storage", async () => {
            // A 5xx (or a proxy's HTML error page) is not "the session had
            // already ended": the refresh token may still be live server-side,
            // so the store must get a real error to warn the user with.
            seedSession();
            fetchMock.mockResolvedValueOnce(emptyResponse(502));

            const err = await provider.signOut().catch((e: unknown) => e);

            expect(err).toBeInstanceOf(Error);
            expect(err).not.toBeInstanceOf(AuthError);
            expect((err as Error).message).toMatch(/logout failed \(HTTP 502\)/);
            expect(storedSession()).toBeNull();
        });

        it("reads the stored session synchronously, so a caller clearing localStorage right after still logs out server-side", async () => {
            seedSession();
            fetchMock.mockResolvedValueOnce(emptyResponse(204));

            const pending = provider.signOut();
            window.localStorage.clear();
            await pending;

            expect(sentForm().get("refresh_token")).toBe("refresh-1");
        });

        it("is a no-op without a session", async () => {
            await expect(provider.signOut()).resolves.toBeUndefined();
            expect(fetchMock).not.toHaveBeenCalled();
        });
    });

    describe("unsupported flows", () => {
        // Called through the interface, as the store does.
        const asProvider = (): AuthProvider => provider;

        it.each([
            ["confirmNewPassword", () => asProvider().confirmNewPassword("x")],
            ["confirmTotpChallenge", () => asProvider().confirmTotpChallenge("123456")],
            ["confirmTotpSetup", () => asProvider().confirmTotpSetup("123456")],
            ["setUpTotp", () => asProvider().setUpTotp("alice")],
            ["verifyTotpSetup", () => asProvider().verifyTotpSetup("123456")],
            ["resetPassword", () => asProvider().resetPassword("alice@example.com")],
            ["confirmResetPassword", () => asProvider().confirmResetPassword({
                email: "a",
                code: "c",
                newPassword: "p"
            })]
        ])("%s throws AuthError UNSUPPORTED", async (_name, call) => {
            const err = await call().catch((e: unknown) => e);

            expect(err).toBeInstanceOf(AuthError);
            expect((err as AuthError).code).toBe("UNSUPPORTED");
            expect(fetchMock).not.toHaveBeenCalled();
        });
    });
});
