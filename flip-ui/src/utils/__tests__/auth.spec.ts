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

import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppConfig } from "../config";

// Mock heavy dependencies so the auth module can be imported in isolation.
vi.mock("aws-amplify/auth", () => ({ fetchAuthSession: vi.fn() }));
vi.mock("aws-amplify/utils", () => ({ Hub: { listen: vi.fn() } }));
vi.mock("@/router", () => ({
    default: { currentRoute: { value: { fullPath: "/" } } },
    routeChange: { gotoLogin: vi.fn() },
}));
vi.mock("@/store/auth", () => ({ useAuthStore: vi.fn(() => ({ $reset: vi.fn() })) }));
vi.mock("@/store/error", () => ({ useErrorStore: vi.fn(() => ({ clearError: vi.fn() })) }));
vi.mock("./snackbar", () => ({ Snackbar: { error: vi.fn() } }));

const mockGetConfig = vi.fn();
vi.mock("../config", async (importOriginal) => {
    const actual = await importOriginal<typeof import("../config")>();
    return { ...actual, getConfig: mockGetConfig };
});

describe("getAuthConfig", () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    it("maps AppConfig fields to the Amplify Cognito configuration shape", async () => {
        const fakeConfig: AppConfig = {
            awsBaseUrl: "http://localhost:8080/api",
            awsUserPoolId: "eu-west-2_TestPool",
            awsClientId: "test-client-id",
            awsRegion: "eu-west-2",
            awsClientSecret: undefined,
            blacklistedModelFiles: "",
            releaseVersion: "",
            maxReimportCount: 10,
            isLocalMode: false,
        };
        mockGetConfig.mockReturnValue(fakeConfig);

        const { getAuthConfig } = await import("../auth");
        const result = getAuthConfig();

        expect(result).toEqual({
            Auth: {
                Cognito: {
                    region: "eu-west-2",
                    userPoolId: "eu-west-2_TestPool",
                    userPoolClientId: "test-client-id",
                    clientSecret: undefined,
                    authenticationFlowType: "USER_PASSWORD_AUTH",
                    loginWith: {},
                },
            },
        });
    });

    it("passes awsClientSecret through to Cognito config when provided", async () => {
        const fakeConfig: AppConfig = {
            awsBaseUrl: "http://localhost:8080/api",
            awsUserPoolId: "eu-west-2_TestPool",
            awsClientId: "test-client-id",
            awsRegion: "eu-west-2",
            awsClientSecret: "super-secret",
            blacklistedModelFiles: "",
            releaseVersion: "",
            maxReimportCount: 10,
            isLocalMode: false,
        };
        mockGetConfig.mockReturnValue(fakeConfig);

        const { getAuthConfig } = await import("../auth");
        const result = getAuthConfig();

        expect(result.Auth.Cognito.clientSecret).toBe("super-secret");
    });

    it("uses the region from AppConfig", async () => {
        const fakeConfig: AppConfig = {
            awsBaseUrl: "http://localhost:8080/api",
            awsUserPoolId: "eu-west-2_TestPool",
            awsClientId: "test-client-id",
            awsRegion: "us-east-1",
            awsClientSecret: undefined,
            blacklistedModelFiles: "",
            releaseVersion: "",
            maxReimportCount: 10,
            isLocalMode: false,
        };
        mockGetConfig.mockReturnValue(fakeConfig);

        const { getAuthConfig } = await import("../auth");
        const result = getAuthConfig();

        expect(result.Auth.Cognito.region).toBe("us-east-1");
    });
});
