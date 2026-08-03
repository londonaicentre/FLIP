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

// Wired into the `prebuild`, `prebuild:deploy` and `prebuild:demo` npm
// hooks so it runs before every `npm run build*`, never before
// `npm run dev`. Vite inlines `import.meta.env.VITE_LOCAL` /
// `import.meta.env.VITE_DEMO` at build time and the surrounding
// branches in src/utils/auth.ts and src/main.ts are
// dead-code-eliminated when the flag is anything but "true" — so any
// production-style build carrying either flag ships a bundle that
// bypasses the Cognito session check on every guarded route and boots
// a MirageJS mock instead of the real backend. The public demo build
// is not exempt: `npm run build:demo` gets its flag from vite.config's
// `define` for `--mode demo`, so VITE_DEMO must never be set in the
// environment at all.

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
 * Same contract as checkViteLocal, for the public-demo flag. VITE_DEMO
 * short-circuits the Cognito session check and boots the offline demo
 * MirageJS server (mocks/demo-server.ts) — an unauthenticated bundle,
 * exactly like VITE_LOCAL. The legitimate demo build never needs the
 * env var: `npm run build:demo` runs `vite build --mode demo`, and
 * vite.config.mts inlines the flag via `define` for that mode.
 *
 * @param {Record<string, string | undefined>} env
 * @returns {string | null}
 */
export function checkViteDemo(env) {
    if (env.VITE_DEMO !== "true") return null;

    return [
        "Refusing to build flip-ui: VITE_DEMO=true is set in the environment.",
        "",
        "VITE_DEMO bypasses the Cognito session check in src/utils/auth.ts",
        "and boots the offline demo MirageJS server from src/main.ts. Vite",
        "inlines this flag at build time, so a production-style build with",
        "VITE_DEMO=true ships an unauthenticated demo bundle in place of",
        "the real app.",
        "",
        "The only supported way to produce the public demo bundle is",
        "'npm run build:demo' (vite build --mode demo), which inlines the",
        "flag itself. Never set VITE_DEMO in the environment or an env file."
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
    const reason = checkViteLocal(env) ?? checkViteDemo(env);
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
