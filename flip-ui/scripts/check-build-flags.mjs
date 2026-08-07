#!/usr/bin/env node
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

// Wired into the `prebuild` and `prebuild:deploy` npm hooks so it runs
// only before `npm run build` / `npm run build:deploy`, never before
// `npm run dev`. Vite inlines `import.meta.env.VITE_LOCAL` at build
// time and the surrounding branches in src/utils/auth.ts and
// src/main.ts are dead-code-eliminated when the flag is "false" — so
// any production-style build with VITE_LOCAL=true ships a bundle that
// bypasses the Cognito session check on every guarded route and
// boots the MirageJS mock instead of the real backend.

import { pathToFileURL } from "node:url";

/**
 * Returns a multi-line error message if the build should be blocked,
 * otherwise null. Exported so the unit test can pin the behaviour
 * without spawning a child process.
 *
 * @param {Record<string, string | undefined>} env
 * @returns {string | null}
 */
export function checkViteLocal(env) {
    if (env.VITE_LOCAL !== "true") return null;

    return [
        "Refusing to build flip-ui: VITE_LOCAL=true is set.",
        "",
        "VITE_LOCAL bypasses the Cognito session check in",
        "src/utils/auth.ts and enables the MirageJS mock in src/main.ts.",
        "Vite inlines this flag at build time, so any production-style",
        "build with VITE_LOCAL=true ships an unauthenticated bundle.",
        "",
        "Unset VITE_LOCAL or set it to 'false' before running",
        "'npm run build' or 'npm run build:deploy'. The flag is only",
        "valid for 'npm run dev' against a mocked API."
    ].join("\n");
}

/**
 * Message for a VITE_E2E misuse. Unlike VITE_LOCAL this flag has one
 * legitimate home — the Cypress dev server (`vite --mode e2e`, reading
 * .env.e2e) — so the caller decides legality from (mode, command) and
 * this only phrases the refusal.
 *
 * VITE_E2E arms the Cypress auth seam in src/utils/auth.ts, which takes
 * the signed-in user from a `cypress.auth.user` localStorage key instead
 * of Cognito. In a browser without that fixture nobody is ever signed in,
 * so every guarded navigation redirects to /auth/login — an interactive
 * dev server with the flag set silently logs you out on each refresh, and
 * a build with it ships an auth seam to users.
 *
 * @param {string} [mode] Vite mode, when known (config-time callers).
 * @param {string} [command] "build" | "serve", when known.
 * @returns {string} multi-line refusal message
 */
export function checkViteE2e(mode, command) {
    const where = mode === undefined
        ? "Refusing to build flip-ui: VITE_E2E=true is set."
        : `Refusing to run flip-ui (mode '${mode}', command '${command}') with VITE_E2E=true.`;

    return [
        where,
        "",
        "VITE_E2E arms the Cypress auth seam in src/utils/auth.ts, which",
        "reads the signed-in user from a 'cypress.auth.user' localStorage",
        "key instead of Cognito. A browser without that fixture is never",
        "signed in, so every page refresh redirects to /auth/login.",
        "",
        "The flag is valid only for the Cypress dev server, which loads",
        "flip-ui/.env.e2e via 'npm run test:start' (vite --mode e2e).",
        "It must never appear in .env.development or in a built bundle."
    ].join("\n");
}

/**
 * CLI driver. Takes the side-effecting bits as parameters so a unit
 * test can drive it without forking a real process — child-process
 * spawns don't count for vitest's coverage instrumentation, and we
 * still want a regression test on the prebuild hook's contract.
 *
 * @param {Record<string, string | undefined>} env
 * @param {{ error: (msg: string) => void, exit: (code: number) => void }} io
 * @returns {0 | 1} exit code; the caller forwards it to process.exit.
 */
export function runCli(env, io) {
    // The prebuild hooks have no Vite mode, so an exported VITE_E2E here is
    // unambiguously a shipping build — refuse it on the same axis as
    // VITE_LOCAL. The mode-aware check lives in vite.config.mts.
    const reason = checkViteLocal(env) ?? (env.VITE_E2E === "true" ? checkViteE2e() : null);
    if (reason) {
        io.error(reason);
        io.exit(1);

        return 1;
    }

    return 0;
}

// Bootstrap: forwards process state into runCli when this file is
// invoked directly (`node scripts/check-build-flags.mjs`). Skipped
// when imported, so vitest's coverage will never light it up — the
// runCli unit tests cover the same logic with injected I/O, and the
// child-process smoke tests verify the wiring end-to-end.
/* v8 ignore start */
const invokedDirectly =
    process.argv[1] !== undefined &&
    import.meta.url === pathToFileURL(process.argv[1]).href;

if (invokedDirectly) {
    runCli(process.env, {
        error: (msg) => console.error(msg),
        exit: (code) => process.exit(code)
    });
}
/* v8 ignore stop */
