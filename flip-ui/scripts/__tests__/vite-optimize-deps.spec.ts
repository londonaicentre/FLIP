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
 * A dependency the dev server optimizes late can abort an in-flight route chunk.
 *
 * Vite's scanner crawls static imports from index.html, so it misses anything
 * reachable only through an unplugin-vue-components registration or a
 * vite-plugin-pages async route. Those get optimized on the request that first
 * pulls them in, and when that invalidates a bundle the browser already holds,
 * Vite full-reloads — killing whatever dynamic import was in flight. That is
 * invisible in normal use (the reload lands on the same route) and fatal under
 * Cypress, where the aborted chunk surfaces as an unhandled rejection from the
 * application and fails the spec.
 *
 * It is not hypothetical: a cold CI cache on develop failed the docs-GIF
 * workflow this way, on "Failed to fetch dynamically imported module:
 * .../src/pages/admin/users.vue", with `date-fns` optimized mid-spec. That run
 * discovered five batches late and reloaded on two of them. Nothing else can
 * catch this — the entries resolve, the app builds, and every other suite
 * passes — so the list is pinned here.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const UI_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

// Every specifier the cold-cache CI run optimized after startup, exactly as Vite
// named it. Removing one puts that batch back into the middle of a Cypress run.
const LATE_DISCOVERED = [
    "codemirror-editor-vue3",
    "codemirror/mode/sql/sql.js",
    "date-fns",
    "highlight.js/lib/core",
    "highlight.js/lib/languages/json",
    "jszip",
    "mime",
    "underscore"
];

const readIncludeList = (): string[] => {
    const source = readFileSync(path.join(UI_ROOT, "vite.config.mts"), "utf8");
    const block = /optimizeDeps:\s*\{\s*include:\s*\[([^\]]*)\]/.exec(source);

    expect(block, "vite.config.mts no longer declares optimizeDeps.include").not.toBeNull();

    return [...block![1].matchAll(/"([^"]+)"/g)].map((match) => match[1]);
};

describe("vite optimizeDeps.include", () => {
    it("pre-bundles every dependency a cold CI cache discovered mid-run", () => {
        expect(readIncludeList()).toEqual(expect.arrayContaining(LATE_DISCOVERED));
    });

    it("lists each specifier once", () => {
        const include = readIncludeList();
        expect(include).toEqual([...new Set(include)]);
    });
});
