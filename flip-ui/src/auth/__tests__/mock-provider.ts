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

import { type Mock, vi } from "vitest";

import type { AuthBackend, AuthCapabilities, AuthProvider } from "@/auth/provider";

/** Every method a `vi.fn()`, so specs assert on provider calls and re-arm them per test. */
export type MockAuthProvider = {
    [K in keyof AuthProvider]: AuthProvider[K] extends (...args: infer A) => infer R
        ? Mock<(...args: A) => R>
        : AuthProvider[K];
};

export const ALL_CAPABILITIES: AuthCapabilities = {
    newPasswordChallenge: true,
    totpChallenge: true,
    totpEnrolment: true,
    forgotPassword: true,
    adminResetPassword: true,
    globalSignOut: true
};

export const NO_CAPABILITIES: AuthCapabilities = {
    newPasswordChallenge: false,
    totpChallenge: false,
    totpEnrolment: false,
    forgotPassword: false,
    adminResetPassword: false,
    globalSignOut: false
};

/**
 * Build a provider whose methods are all spies. Defaults describe a signed-out
 * Cognito-shaped backend: `hasSession` false, `getAccessToken` null, and
 * `onSessionExpired` returning a no-op unsubscribe.
 */
export function makeMockAuthProvider(overrides: {
    backend?: AuthBackend;
    capabilities?: Partial<AuthCapabilities>;
} = {}): MockAuthProvider {
    const provider: MockAuthProvider = {
        backend: overrides.backend ?? "cognito",
        capabilities: {
            ...ALL_CAPABILITIES,
            ...overrides.capabilities
        },
        configure: vi.fn(),
        signIn: vi.fn(),
        confirmNewPassword: vi.fn(),
        confirmTotpChallenge: vi.fn(),
        confirmTotpSetup: vi.fn(),
        setUpTotp: vi.fn(),
        verifyTotpSetup: vi.fn(),
        getAccessToken: vi.fn(async () => null),
        getUser: vi.fn(),
        hasSession: vi.fn(async () => false),
        signOut: vi.fn(async () => undefined),
        resetPassword: vi.fn(async () => undefined),
        confirmResetPassword: vi.fn(async () => undefined),
        onSessionExpired: vi.fn(() => () => undefined)
    };

    return provider;
}

/** `mockReset()` every spy and restore the signed-out defaults. */
export function resetMockAuthProvider(provider: MockAuthProvider): void {
    for (const key of Object.keys(provider) as (keyof MockAuthProvider)[]) {
        const member = provider[key];
        if (typeof member === "function" && "mockReset" in member) {
            (member as Mock).mockReset();
        }
    }
    provider.getAccessToken.mockResolvedValue(null);
    provider.hasSession.mockResolvedValue(false);
    provider.signOut.mockResolvedValue(undefined);
    provider.resetPassword.mockResolvedValue(undefined);
    provider.confirmResetPassword.mockResolvedValue(undefined);
    provider.onSessionExpired.mockReturnValue(() => undefined);
}
