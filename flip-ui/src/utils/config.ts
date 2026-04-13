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
 * Centralized application configuration module.
 *
 * Configuration is resolved from two sources in priority order:
 *
 * 1. **`window.__FLIP_CONFIG__`** (runtime) — for static hosting environments
 *    such as S3+CloudFront, where a `/config.js` file is generated per
 *    environment by the deployment pipeline and served alongside the SPA
 *    bundle. Allows deploying a single build artifact to multiple environments.
 *
 * 2. **`import.meta.env`** (build-time) — for Vite dev-server and current
 *    Docker/EC2-based deployments where VITE_* variables are available at
 *    startup. Values for keys absent from window.__FLIP_CONFIG__ fall back here.
 *
 * Required variables (application will not start without these):
 *   - VITE_AWS_BASE_URL
 *   - VITE_AWS_USER_POOL_ID
 *   - VITE_AWS_CLIENT_ID
 *
 * Optional variables (have sensible defaults):
 *   - VITE_AWS_REGION              (default: "eu-west-2")
 *   - VITE_AWS_CLIENT_SECRET       (default: undefined — only set for Cognito
 *                                   pools with a client secret; note that all
 *                                   VITE_* vars are embedded in the bundle at
 *                                   build time, so avoid using this for
 *                                   confidential values in static deployments)
 *   - VITE_BLACKLISTED_MODEL_FILES (default: "")
 *   - VITE_RELEASE_VERSION         (default: "")
 *   - VITE_MAX_REIMPORT_COUNT      (default: 10)
 *   - VITE_LOCAL                   (default: "false")
 */

export interface AppConfig {
    awsBaseUrl: string;
    awsUserPoolId: string;
    awsClientId: string;
    awsRegion: string;
    awsClientSecret: string | undefined;
    blacklistedModelFiles: string;
    releaseVersion: string;
    maxReimportCount: number;
    isLocalMode: boolean;
}

const CONFIG_KEYS = [
    "VITE_AWS_BASE_URL",
    "VITE_AWS_USER_POOL_ID",
    "VITE_AWS_CLIENT_ID",
    "VITE_AWS_REGION",
    "VITE_AWS_CLIENT_SECRET",
    "VITE_BLACKLISTED_MODEL_FILES",
    "VITE_RELEASE_VERSION",
    "VITE_MAX_REIMPORT_COUNT",
    "VITE_LOCAL",
] as const;

const REQUIRED_VARS = ["VITE_AWS_BASE_URL", "VITE_AWS_USER_POOL_ID", "VITE_AWS_CLIENT_ID"] as const;

/**
 * Merges runtime (window.__FLIP_CONFIG__) and build-time (import.meta.env) config sources.
 * Runtime values take precedence; build-time values are used as fallback.
 */
function resolveEnv(): Record<string, string | undefined> {
    const runtime: Record<string, string | undefined> =
        typeof window !== "undefined" && window.__FLIP_CONFIG__ ? window.__FLIP_CONFIG__ : {};
    const buildtime = import.meta.env as Record<string, string | undefined>;

    const resolved: Record<string, string | undefined> = {};
    for (const key of CONFIG_KEYS) {
        resolved[key] = runtime[key] ?? buildtime[key];
    }
    return resolved;
}

/**
 * Validates that all required environment variables are present.
 * Throws an error listing all missing variables if any are absent.
 */
function validateConfig(env: Record<string, string | undefined>): void {
    const missing = REQUIRED_VARS.filter((key) => !env[key]);

    if (missing.length > 0) {
        throw new Error(
            `Missing required environment variables: ${missing.join(", ")}. ` +
            "Please set these in your .env.development file (development) or " +
            "via your hosting environment (production). " +
            "See flip-ui/README.md for configuration details."
        );
    }
}

/** Cached config singleton — built once on first call and reused thereafter. */
let _cached: AppConfig | null = null;

/**
 * Builds and returns the validated application configuration.
 *
 * Reads from window.__FLIP_CONFIG__ first (runtime injection, suitable for
 * S3+CloudFront), then from import.meta.env (Vite build-time substitution).
 * Fails fast with a descriptive error if required variables are absent.
 * The result is memoized; subsequent calls return the same object.
 */
export function getConfig(): AppConfig {
    if (_cached) return _cached;

    const env = resolveEnv();

    validateConfig(env);

    const rawMaxReimport = env["VITE_MAX_REIMPORT_COUNT"];
    const maxReimportCount = rawMaxReimport ? parseInt(rawMaxReimport, 10) : 10;

    _cached = {
        awsBaseUrl: env["VITE_AWS_BASE_URL"] as string,
        awsUserPoolId: env["VITE_AWS_USER_POOL_ID"] as string,
        awsClientId: env["VITE_AWS_CLIENT_ID"] as string,
        awsRegion: env["VITE_AWS_REGION"] ?? "eu-west-2",
        awsClientSecret: env["VITE_AWS_CLIENT_SECRET"],
        blacklistedModelFiles: env["VITE_BLACKLISTED_MODEL_FILES"] ?? "",
        releaseVersion: env["VITE_RELEASE_VERSION"] ?? "",
        maxReimportCount: isNaN(maxReimportCount) ? 10 : maxReimportCount,
        isLocalMode: env["VITE_LOCAL"] === "true",
    };

    return _cached;
}

/**
 * Clears the memoized config singleton.
 * **For use in tests only** — do not call in application code.
 * @internal
 */
export function _resetConfigForTesting(): void {
    _cached = null;
}
