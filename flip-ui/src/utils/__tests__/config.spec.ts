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

import { _resetConfigForTesting, getConfig } from "../config";

const BASE_ENV = {
    VITE_AWS_BASE_URL: "http://localhost:8080/api",
    VITE_AWS_USER_POOL_ID: "eu-west-2_TestPool",
    VITE_AWS_CLIENT_ID: "testclientid123",
};

function setImportMetaEnv(overrides: Record<string, string | undefined>) {
    vi.stubEnv("VITE_AWS_BASE_URL", overrides["VITE_AWS_BASE_URL"] ?? "");
    vi.stubEnv("VITE_AWS_USER_POOL_ID", overrides["VITE_AWS_USER_POOL_ID"] ?? "");
    vi.stubEnv("VITE_AWS_CLIENT_ID", overrides["VITE_AWS_CLIENT_ID"] ?? "");
    if (overrides["VITE_AWS_REGION"] !== undefined) vi.stubEnv("VITE_AWS_REGION", overrides["VITE_AWS_REGION"]);
    if (overrides["VITE_AWS_CLIENT_SECRET"] !== undefined) vi.stubEnv("VITE_AWS_CLIENT_SECRET", overrides["VITE_AWS_CLIENT_SECRET"]);
    if (overrides["VITE_BLACKLISTED_MODEL_FILES"] !== undefined) vi.stubEnv("VITE_BLACKLISTED_MODEL_FILES", overrides["VITE_BLACKLISTED_MODEL_FILES"]);
    if (overrides["VITE_RELEASE_VERSION"] !== undefined) vi.stubEnv("VITE_RELEASE_VERSION", overrides["VITE_RELEASE_VERSION"]);
    if (overrides["VITE_MAX_REIMPORT_COUNT"] !== undefined) vi.stubEnv("VITE_MAX_REIMPORT_COUNT", overrides["VITE_MAX_REIMPORT_COUNT"]);
    if (overrides["VITE_LOCAL"] !== undefined) vi.stubEnv("VITE_LOCAL", overrides["VITE_LOCAL"]);
}

describe("getConfig", () => {
    beforeEach(() => {
        vi.unstubAllEnvs();
        _resetConfigForTesting();
    });

    afterEach(() => {
        vi.unstubAllEnvs();
        delete window.__FLIP_CONFIG__;
        _resetConfigForTesting();
    });

    // ── Required variable validation ─────────────────────────────────────────

    describe("required variable validation", () => {
        it("throws when VITE_AWS_BASE_URL is missing", () => {
            setImportMetaEnv({ ...BASE_ENV, VITE_AWS_BASE_URL: "" });
            expect(() => getConfig()).toThrowError(/VITE_AWS_BASE_URL/);
        });

        it("throws when VITE_AWS_USER_POOL_ID is missing", () => {
            setImportMetaEnv({ ...BASE_ENV, VITE_AWS_USER_POOL_ID: "" });
            expect(() => getConfig()).toThrowError(/VITE_AWS_USER_POOL_ID/);
        });

        it("throws when VITE_AWS_CLIENT_ID is missing", () => {
            setImportMetaEnv({ ...BASE_ENV, VITE_AWS_CLIENT_ID: "" });
            expect(() => getConfig()).toThrowError(/VITE_AWS_CLIENT_ID/);
        });

        it("lists all missing required variables in a single error", () => {
            setImportMetaEnv({ VITE_AWS_BASE_URL: "", VITE_AWS_USER_POOL_ID: "", VITE_AWS_CLIENT_ID: "" });
            expect(() => getConfig()).toThrowError(/VITE_AWS_BASE_URL.*VITE_AWS_USER_POOL_ID.*VITE_AWS_CLIENT_ID/);
        });

        it("error message includes setup guidance", () => {
            setImportMetaEnv({ ...BASE_ENV, VITE_AWS_BASE_URL: "" });
            expect(() => getConfig()).toThrowError(/\.env\.development/);
        });
    });

    // ── Required variable values ──────────────────────────────────────────────

    describe("required variable values", () => {
        it("returns correct awsBaseUrl", () => {
            setImportMetaEnv(BASE_ENV);
            expect(getConfig().awsBaseUrl).toBe("http://localhost:8080/api");
        });

        it("returns correct awsUserPoolId", () => {
            setImportMetaEnv(BASE_ENV);
            expect(getConfig().awsUserPoolId).toBe("eu-west-2_TestPool");
        });

        it("returns correct awsClientId", () => {
            setImportMetaEnv(BASE_ENV);
            expect(getConfig().awsClientId).toBe("testclientid123");
        });
    });

    // ── Optional variables with defaults ─────────────────────────────────────

    describe("optional variables with defaults", () => {
        beforeEach(() => setImportMetaEnv(BASE_ENV));

        it("defaults awsRegion to eu-west-2 when not set", () => {
            expect(getConfig().awsRegion).toBe("eu-west-2");
        });

        it("uses provided awsRegion when set", () => {
            setImportMetaEnv({ ...BASE_ENV, VITE_AWS_REGION: "us-east-1" });
            expect(getConfig().awsRegion).toBe("us-east-1");
        });

        it("defaults awsClientSecret to undefined when not set", () => {
            expect(getConfig().awsClientSecret).toBeUndefined();
        });

        it("uses provided awsClientSecret when set", () => {
            setImportMetaEnv({ ...BASE_ENV, VITE_AWS_CLIENT_SECRET: "mysecret" });
            expect(getConfig().awsClientSecret).toBe("mysecret");
        });

        it("defaults blacklistedModelFiles to empty string", () => {
            expect(getConfig().blacklistedModelFiles).toBe("");
        });

        it("uses provided blacklistedModelFiles", () => {
            setImportMetaEnv({ ...BASE_ENV, VITE_BLACKLISTED_MODEL_FILES: "flip.py,server_app.py" });
            expect(getConfig().blacklistedModelFiles).toBe("flip.py,server_app.py");
        });

        it("defaults releaseVersion to empty string", () => {
            expect(getConfig().releaseVersion).toBe("");
        });

        it("uses provided releaseVersion", () => {
            setImportMetaEnv({ ...BASE_ENV, VITE_RELEASE_VERSION: "1.2.3" });
            expect(getConfig().releaseVersion).toBe("1.2.3");
        });

        it("defaults maxReimportCount to 10", () => {
            expect(getConfig().maxReimportCount).toBe(10);
        });

        it("uses provided maxReimportCount", () => {
            setImportMetaEnv({ ...BASE_ENV, VITE_MAX_REIMPORT_COUNT: "20" });
            expect(getConfig().maxReimportCount).toBe(20);
        });

        it("falls back to default 10 when VITE_MAX_REIMPORT_COUNT is not a number", () => {
            setImportMetaEnv({ ...BASE_ENV, VITE_MAX_REIMPORT_COUNT: "notanumber" });
            expect(getConfig().maxReimportCount).toBe(10);
        });

        it("defaults isLocalMode to false", () => {
            expect(getConfig().isLocalMode).toBe(false);
        });

        it("sets isLocalMode to true when VITE_LOCAL is 'true'", () => {
            setImportMetaEnv({ ...BASE_ENV, VITE_LOCAL: "true" });
            expect(getConfig().isLocalMode).toBe(true);
        });

        it("keeps isLocalMode false for non-'true' values of VITE_LOCAL", () => {
            setImportMetaEnv({ ...BASE_ENV, VITE_LOCAL: "1" });
            expect(getConfig().isLocalMode).toBe(false);
        });
    });

    // ── Runtime config (window.__FLIP_CONFIG__) ───────────────────────────────

    describe("runtime config (window.__FLIP_CONFIG__)", () => {
        beforeEach(() => setImportMetaEnv(BASE_ENV));

        it("runtime config takes precedence over build-time env for required vars", () => {
            window.__FLIP_CONFIG__ = {
                VITE_AWS_BASE_URL: "https://runtime.example.com/api",
                VITE_AWS_USER_POOL_ID: "eu-west-2_RuntimePool",
                VITE_AWS_CLIENT_ID: "runtime-client-id",
            };
            const cfg = getConfig();
            expect(cfg.awsBaseUrl).toBe("https://runtime.example.com/api");
            expect(cfg.awsUserPoolId).toBe("eu-west-2_RuntimePool");
            expect(cfg.awsClientId).toBe("runtime-client-id");
        });

        it("falls back to build-time env for keys absent from runtime config", () => {
            window.__FLIP_CONFIG__ = {
                VITE_AWS_BASE_URL: "https://runtime.example.com/api",
                VITE_AWS_USER_POOL_ID: "eu-west-2_RuntimePool",
                VITE_AWS_CLIENT_ID: "runtime-client-id",
                // VITE_AWS_REGION not set — falls back to import.meta.env default
            };
            expect(getConfig().awsRegion).toBe("eu-west-2");
        });

        it("runtime config can override optional variables", () => {
            window.__FLIP_CONFIG__ = {
                VITE_AWS_BASE_URL: "https://runtime.example.com/api",
                VITE_AWS_USER_POOL_ID: "eu-west-2_RuntimePool",
                VITE_AWS_CLIENT_ID: "runtime-client-id",
                VITE_AWS_REGION: "us-east-1",
                VITE_MAX_REIMPORT_COUNT: "25",
                VITE_BLACKLISTED_MODEL_FILES: "foo.py,bar.py",
            };
            const cfg = getConfig();
            expect(cfg.awsRegion).toBe("us-east-1");
            expect(cfg.maxReimportCount).toBe(25);
            expect(cfg.blacklistedModelFiles).toBe("foo.py,bar.py");
        });

        it("still validates required vars even when runtime config is present", () => {
            window.__FLIP_CONFIG__ = {
                VITE_AWS_BASE_URL: "https://runtime.example.com/api",
                // VITE_AWS_USER_POOL_ID and VITE_AWS_CLIENT_ID omitted
            };
            // Also clear build-time env so there is no fallback
            setImportMetaEnv({ VITE_AWS_BASE_URL: "", VITE_AWS_USER_POOL_ID: "", VITE_AWS_CLIENT_ID: "" });
            expect(() => getConfig()).toThrowError(/VITE_AWS_USER_POOL_ID/);
        });

        it("works correctly when window.__FLIP_CONFIG__ is not set at all", () => {
            // No window.__FLIP_CONFIG__ — falls back to import.meta.env entirely
            setImportMetaEnv(BASE_ENV);
            const cfg = getConfig();
            expect(cfg.awsBaseUrl).toBe("http://localhost:8080/api");
        });

        it("partially overrides: runtime keys win, build-time keys fill in the rest", () => {
            setImportMetaEnv({ ...BASE_ENV, VITE_RELEASE_VERSION: "build-version" });
            window.__FLIP_CONFIG__ = {
                VITE_AWS_BASE_URL: "https://runtime.example.com/api",
                VITE_AWS_USER_POOL_ID: "eu-west-2_RuntimePool",
                VITE_AWS_CLIENT_ID: "runtime-client-id",
                // VITE_RELEASE_VERSION not in runtime config
            };
            const cfg = getConfig();
            expect(cfg.awsBaseUrl).toBe("https://runtime.example.com/api");  // runtime wins
            expect(cfg.releaseVersion).toBe("build-version");                 // fallback to build-time
        });
    });

    // ── Memoization ───────────────────────────────────────────────────────────

    describe("memoization", () => {
        it("returns the same object reference on subsequent calls", () => {
            setImportMetaEnv(BASE_ENV);
            const first = getConfig();
            const second = getConfig();
            expect(first).toBe(second);
        });

        it("_resetConfigForTesting clears the cache so the next call re-reads env", () => {
            setImportMetaEnv({ ...BASE_ENV, VITE_RELEASE_VERSION: "v1" });
            expect(getConfig().releaseVersion).toBe("v1");

            _resetConfigForTesting();
            vi.unstubAllEnvs();

            setImportMetaEnv({ ...BASE_ENV, VITE_RELEASE_VERSION: "v2" });
            expect(getConfig().releaseVersion).toBe("v2");
        });
    });
});
