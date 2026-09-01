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
 * The artefact check is the last line between the recorded Ark+ register and a
 * public CloudFront origin, so its own failure modes matter: a check that
 * silently passes on an empty scan, or whose sentinels drift out of the
 * register, is worse than no check because it reads as evidence.
 */

import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { demoUserId, runCli, scan, sentinels } from "../assert-no-demo-artefacts.mjs";

let dist: string;

function makeIo() {
    return { log: vi.fn(), error: vi.fn(), exit: vi.fn() };
}

/** Write a file into the fake dist tree, creating parents. */
function write(relPath: string, content: string): void {
    const full = path.join(dist, relPath);
    mkdirSync(path.dirname(full), { recursive: true });
    writeFileSync(full, content);
}

beforeEach(() => {
    dist = mkdtempSync(path.join(tmpdir(), "flip-artefact-check-"));
});

afterEach(() => {
    rmSync(dist, { recursive: true, force: true });
});

describe("sentinels", () => {
    it("reads the demo user id out of the register instead of hardcoding it", () => {
        // If this drifts, the check is asserting on a value nothing emits and
        // would pass a build that genuinely leaked.
        expect(demoUserId()).toMatch(/^[0-9a-f-]{36}$/i);
        expect(sentinels().some(s => s.needle === demoUserId())).toBe(true);
    });

    it("covers each class of recorded content", () => {
        const needles = sentinels().map(s => s.needle);
        expect(needles).toContain("WITH project_images AS");
        expect(needles).toContain("Bangkok Dusit Medical Services");
        expect(needles).toContain("Running in Ark+ demo mode");
    });
});

describe("scan", () => {
    it("reports no hits for a clean build", () => {
        write("assets/index-abc123.js", "console.log('nothing to see');");
        expect(scan(dist).hits).toHaveLength(0);
    });

    it("finds the register in an entry chunk", () => {
        write("assets/index-abc123.js", `const u="${demoUserId()}";`);

        const { hits } = scan(dist);
        expect(hits).toHaveLength(1);
        expect(hits[0].file).toContain("index-abc123.js");
    });

    it("finds the register in a lazily-loaded chunk, not just the entry", () => {
        // A dynamic import moves the fixtures into their own chunk. That chunk
        // is still published and still publicly fetchable, so it must fail too.
        write("assets/demo-server-xyz.js", "const sql='WITH project_images AS (...)';");
        expect(scan(dist).hits).toHaveLength(1);
    });

    it("scans nested directories and non-JS text output", () => {
        write("static/nested/deep/chunk.js", "'Bangkok Dusit Medical Services'");
        write("index.html", "<!-- Running in Ark+ demo mode -->");

        expect(scan(dist).hits).toHaveLength(2);
    });

    it("ignores binary asset formats", () => {
        write("assets/font.woff2", `binary ${demoUserId()} blob`);
        expect(scan(dist).hits).toHaveLength(0);
    });
});

describe("runCli", () => {
    it("exits 0 and reports the count scanned when clean", () => {
        write("assets/index.js", "clean");
        const io = makeIo();

        expect(runCli([dist], io)).toBe(0);
        expect(io.exit).not.toHaveBeenCalled();
        expect(io.log.mock.calls[0][0]).toContain("OK");
    });

    it("exits 1 and names the offending file when a sentinel is present", () => {
        write("assets/index.js", `const u="${demoUserId()}";`);
        const io = makeIo();

        expect(runCli([dist], io)).toBe(1);
        expect(io.exit).toHaveBeenCalledWith(1);
        expect(io.error.mock.calls.map(c => c[0]).join("\n")).toContain("index.js");
    });

    it("fails rather than passing vacuously when there is no build output", () => {
        // The dangerous failure mode: "no files scanned" must never read as
        // "no leak found".
        const io = makeIo();

        expect(runCli([path.join(dist, "does-not-exist")], io)).toBe(1);
        expect(io.exit).toHaveBeenCalledWith(1);
        expect(io.error.mock.calls[0][0]).toContain("no build output");
    });
});
