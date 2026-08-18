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
 * Fails a NON-demo build whose output still carries the Ark+ demo register.
 *
 * scripts/check-build-flags.mjs inspects the *environment* — it proves the
 * build was invoked without VITE_DEMO. This script inspects the *artefact*,
 * which is the thing actually published. The two are not equivalent: the demo
 * register once shipped inside the public production entry chunk from a build
 * whose environment was entirely clean, because the branch guarding it was a
 * cross-module const that rolldown could not fold away (FLIP#794 review). Only
 * an output check catches that class of regression.
 *
 * Runs from `postbuild:deploy`, i.e. on exactly the build `make deploy-ui`
 * publishes to the production CloudFront origin. It deliberately does NOT run
 * on `build:demo`, where every sentinel below is expected to be present.
 *
 * Usage: node scripts/assert-no-demo-artefacts.mjs [distDir]
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const UI_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const REGISTER = join(UI_ROOT, "mocks/demo/ark-plus-register.ts");

/**
 * The demo viewer's user id, read from the register rather than duplicated, so
 * a re-capture or a re-scrub of that constant cannot leave this check asserting
 * on a value nothing emits any more.
 */
export function demoUserId() {
    const source = readFileSync(REGISTER, "utf8");
    const match = /export const DEMO_USER = \{\s*\n\s*id: "([^"]+)"/.exec(source);

    if (match === null) {
        throw new Error(
            `Could not read DEMO_USER.id from ${relative(UI_ROOT, REGISTER)} — the constant was renamed or `
            + "reshaped. Update this script together with it; do not delete the check."
        );
    }

    return match[1];
}

/**
 * Strings that exist ONLY in the recorded register, each standing in for one
 * class of leaked content. Model and project ids are deliberately absent: they
 * are public URL path segments carried by src/demo/bootstrap.ts (the demo
 * download map), which every build legitimately contains.
 */
export function sentinels() {
    return [
        { label: "demo viewer user id (a Cognito sub shape)", needle: demoUserId() },
        { label: "recorded OMOP cohort SQL", needle: "WITH project_images AS" },
        { label: "recorded trust roster", needle: "Bangkok Dusit Medical Services" },
        { label: "recorded trust roster", needle: "AI Centre Private" },
        { label: "demo-mode boot branch", needle: "Running in Ark+ demo mode" }
    ];
}

function walk(dir) {
    const out = [];

    for (const entry of readdirSync(dir)) {
        const full = join(dir, entry);

        if (statSync(full).isDirectory()) {
            out.push(...walk(full));
        }
        else {
            out.push(full);
        }
    }

    return out;
}

/**
 * Scan `distDir` and return every sentinel occurrence found.
 *
 * @returns {{ file: string, label: string, needle: string }[]} empty when clean.
 */
export function scan(distDir) {
    // Text formats only: a leak has to be readable to be a leak, and hashing
    // every font/image wastes the budget this check is meant to fit inside.
    const scannable = walk(distDir).filter(f => /\.(js|mjs|cjs|css|html|json|map|txt)$/i.test(f));
    const hits = [];

    for (const file of scannable) {
        const content = readFileSync(file, "utf8");

        for (const { label, needle } of sentinels()) {
            if (content.includes(needle)) {
                hits.push({ file: relative(UI_ROOT, file), label, needle });
            }
        }
    }

    return { hits, scanned: scannable.length };
}

export function runCli(argv, io) {
    const distDir = resolve(UI_ROOT, argv[0] ?? "dist");
    let result;

    try {
        result = scan(distDir);
    }
    catch {
        io.error(`assert-no-demo-artefacts: ERROR — no build output at ${distDir}`);
        io.exit(1);

        return 1;
    }

    if (result.hits.length > 0) {
        io.error("assert-no-demo-artefacts: FAILED — Ark+ demo register found in a non-demo build.\n");

        for (const { file, label, needle } of result.hits) {
            io.error(`  ${file}\n    ${label}: ${JSON.stringify(needle)}`);
        }

        io.error(
            "\nThis output is published unauthenticated to the production CloudFront origin.\n"
            + "Cause is almost always a demo branch guarded by a value rolldown cannot fold at\n"
            + "build time — gate on the `import.meta.env.VITE_DEMO === \"true\"` literal in the\n"
            + "same module, never on a const imported from another one. See src/main.ts.\n"
        );
        io.exit(1);

        return 1;
    }

    io.log(
        `assert-no-demo-artefacts: OK — ${sentinels().length} sentinels absent from `
        + `${result.scanned} files in ${relative(UI_ROOT, distDir)}/`
    );

    return 0;
}

// Bootstrap: only when invoked directly, so importing this module from a spec
// never scans or exits. Mirrors check-build-flags.mjs.
/* v8 ignore start */
const invokedDirectly =
    process.argv[1] !== undefined &&
    import.meta.url === pathToFileURL(process.argv[1]).href;

if (invokedDirectly) {
    runCli(process.argv.slice(2), {
        log: (msg) => console.log(msg),
        error: (msg) => console.error(msg),
        exit: (code) => process.exit(code)
    });
}
/* v8 ignore stop */
