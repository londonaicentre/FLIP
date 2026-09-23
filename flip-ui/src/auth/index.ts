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
 * Backend selection for the auth seam (FLIP#919).
 *
 * `getAuthProvider()` is the ONLY way production code obtains a provider. It
 * reads `window.AUTH_BACKEND` — emitted into the runtime-generated
 * `js/window.js` by scripts/generate-window-js.sh from the hub's own
 * `AUTH_BACKEND` setting — so the UI and flip-api can never disagree on which
 * identity provider is in play.
 *
 * Value symbols (`SignInStep`, `AuthError`, ...) are deliberately NOT
 * re-exported here: they live in the dependency-free `@/auth/provider`, so a
 * spec can mock this module with a bare factory and still import the real
 * classes. Only types are re-exported for convenience.
 */

import { CognitoAuthProvider } from "./cognito-provider";
import { KeycloakAuthProvider } from "./keycloak-provider";
import type { AuthBackend, AuthProvider } from "./provider";

export type {
    AuthBackend,
    AuthCapabilities,
    AuthProvider,
    AuthUser,
    SignInResult,
    TotpSetupDetails
} from "./provider";

let instance: AuthProvider | null = null;

/** Map the raw window value to a backend name; unset/empty means Cognito. */
export function resolveAuthBackend(raw: unknown): AuthBackend {
    const value = typeof raw === "string" ? raw.trim().toLowerCase() : "";
    if (value === "" || value === "cognito") {
        return "cognito";
    }
    if (value === "keycloak") {
        return "keycloak";
    }
    throw new Error(
        `Unknown AUTH_BACKEND "${String(raw)}" in js/window.js: expected "cognito" or "keycloak". ` +
        "Set AUTH_BACKEND in the hub env file to match flip-api and regenerate window.js " +
        "(scripts/generate-window-js.sh)."
    );
}

/** Lazy singleton: the provider for the configured backend. */
export function getAuthProvider(): AuthProvider {
    if (instance === null) {
        const backend = resolveAuthBackend(window.AUTH_BACKEND);
        instance = backend === "keycloak" ? new KeycloakAuthProvider() : new CognitoAuthProvider();
    }

    return instance;
}

/** Drop the cached provider so a spec can re-select under a different window config. */
export function __resetAuthProviderForTests(): void {
    instance = null;
}
