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

import { __resetAuthProviderForTests, getAuthProvider, resolveAuthBackend } from "@/auth";

// Neither provider may do any work at construction — the selector must be
// safe to call at import time from any module. Stub both classes so the spec
// asserts on the selection alone.
vi.mock("@/auth/cognito-provider", () => ({
    CognitoAuthProvider: class {
        backend = "cognito";
    }
}));
vi.mock("@/auth/keycloak-provider", () => ({
    KeycloakAuthProvider: class {
        backend = "keycloak";
    }
}));

// env.d.ts declares window.AUTH_BACKEND as a required string; the specs need
// it absent, hence the untyped delete.
const win = window as unknown as Record<string, unknown>;

describe("resolveAuthBackend", () => {
    it("defaults to cognito when the value is unset or empty", () => {
        expect(resolveAuthBackend(undefined)).toBe("cognito");
        expect(resolveAuthBackend("")).toBe("cognito");
        expect(resolveAuthBackend("   ")).toBe("cognito");
    });

    it("accepts the two known backends, case-insensitively", () => {
        expect(resolveAuthBackend("cognito")).toBe("cognito");
        expect(resolveAuthBackend("keycloak")).toBe("keycloak");
        expect(resolveAuthBackend("Keycloak")).toBe("keycloak");
    });

    it("rejects anything else with an actionable message", () => {
        expect(() => resolveAuthBackend("okta")).toThrow(/AUTH_BACKEND/);
        expect(() => resolveAuthBackend("okta")).toThrow(/"okta"/);
        expect(() => resolveAuthBackend("okta")).toThrow(/cognito.*keycloak/);
        expect(() => resolveAuthBackend("okta")).toThrow(/window\.js/);
    });
});

describe("getAuthProvider", () => {
    beforeEach(() => {
        __resetAuthProviderForTests();
        delete win.AUTH_BACKEND;
    });

    afterEach(() => {
        __resetAuthProviderForTests();
        delete win.AUTH_BACKEND;
    });

    it("selects the Cognito provider when window.AUTH_BACKEND is unset", () => {
        expect(getAuthProvider().backend).toBe("cognito");
    });

    it("selects the Keycloak provider when window.AUTH_BACKEND is 'keycloak'", () => {
        win.AUTH_BACKEND = "keycloak";

        expect(getAuthProvider().backend).toBe("keycloak");
    });

    it("is a singleton: the same instance comes back on every call", () => {
        const first = getAuthProvider();

        expect(getAuthProvider()).toBe(first);
    });

    it("throws on an unknown backend and does not cache the failure", () => {
        win.AUTH_BACKEND = "saml";

        expect(() => getAuthProvider()).toThrow(/AUTH_BACKEND/);

        win.AUTH_BACKEND = "keycloak";
        expect(getAuthProvider().backend).toBe("keycloak");
    });
});
