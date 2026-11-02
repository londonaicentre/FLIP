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

import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { assertClean, checkDist, checkFixtures, demoId, ROSTER, scrubFiles } from "../demo-fixtures.mjs";

// Raw captures are arbitrary JSON that the tests reach into freely.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Json = any;

// A raw capture as the hub serves it, before the scrub: production-style names,
// a dev admin user, one participant per roster site, one non-participant
// (mapped to null) that answered the cohort query, and real-looking ids.
const PROD_A = {
    id: "f43abcd0-be77-4dc8-89c6-4b8aa98b6183",
    name: "Bangkok Dusit Medical Services",
    code: "BDMS"
};
const PROD_B = {
    id: "16f5b300-8ff4-4f1a-9a60-1989b3b54489",
    name: "King's College London",
    code: "KCL"
};
const OTHER = {
    id: "7c0f3e4b-1111-4222-8333-444455556666",
    name: "AI Centre Private",
    code: "AICP"
};
const STRAY = {
    id: "99999999-9999-4999-8999-999999999999",
    name: "Elsewhere Health",
    code: "ELSE"
};
const PROJECT_ID = "2b837f54-8052-4bfa-b75b-c05f036e6086";
const MODEL_ID = "31569b0b-7698-4bf4-a446-59643c6674e0";
const QUERY_ID = "e5e752c4-9fd7-41e2-934d-50fd14125116";
const FILE_ID = "065bbdf2-0c4b-4fad-9443-ca057441a873";
const ADMIN = {
    id: "0f1b6a2e-0001-4919-8000-000000000001",
    email: "aicentreflip@gmail.com",
    name: "",
    organisation: "",
    isDisabled: false
};
const TRUST_MAP = {
    [PROD_A.id]: ROSTER.sites[0].id,
    [PROD_B.id]: ROSTER.sites[1].id,
    [OTHER.id]: null
};
const HEADER = "-- Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London\n"
    + "-- Licensed under the Apache License, Version 2.0 (the \"License\");\n"
    + "--     http://www.apache.org/licenses/LICENSE-2.0\n"
    + "-- limitations under the License.\n\n";

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

function rawFiles(): Record<string, Json> {
    return {
        "project.json": {
            id: PROJECT_ID,
            name: "Ark+ Finetuning",
            ownerId: ADMIN.id,
            ownerEmail: ADMIN.email,
            ownerName: null,
            userCount: 0,
            users: [ADMIN],
            approvedTrusts: [{
                ...PROD_A,
                status: "APPROVED"
            }, {
                ...PROD_B,
                status: "APPROVED"
            }],
            query: {
                id: QUERY_ID,
                query: HEADER + "WITH project_images AS (SELECT 1)",
                queriedTrustIds: [PROD_A.id, PROD_B.id, OTHER.id],
                respondedTrustIds: [PROD_A.id, PROD_B.id, OTHER.id],
                totalCohort: 1600,
                createdBy: "AI Centre Admin"
            },
            creationtimestamp: "2026-08-21T17:10:17.523Z"
        },
        "models.json": {
            data: [{
                id: MODEL_ID,
                name: "Ark+ run",
                owner_id: ADMIN.id
            }]
        },
        "cohort.json": {
            recordCount: 1600,
            trustsResults: [{
                name: "Counts",
                results: [
                    {
                        trustId: PROD_A.id,
                        trustName: PROD_A.name,
                        data: []
                    },
                    {
                        trustId: PROD_B.id,
                        trustName: PROD_B.name,
                        data: []
                    },
                    {
                        trustId: OTHER.id,
                        trustName: OTHER.name,
                        data: []
                    }
                ]
            }],
            trustRecordCounts: {
                [PROD_A.id]: 600,
                [PROD_B.id]: 500,
                [OTHER.id]: 500
            },
            trustSuppressed: [OTHER.id],
            trustErrors: { [OTHER.id]: "timeout" }
        },
        "image_status.json": [
            {
                trustId: PROD_A.id,
                trustName: PROD_A.name
            },
            {
                trustId: PROD_B.id,
                trustName: PROD_B.name
            }
        ],
        "m1_step.json": {
            modelId: MODEL_ID,
            projectId: PROJECT_ID,
            trusts: [PROD_A, PROD_B],
            query: {
                id: QUERY_ID,
                query: HEADER + "SELECT 1"
            }
        },
        "m1_logs.json": [{
            modelId: MODEL_ID,
            log: `Selected trusts for training: ${PROD_B.name}, ${PROD_A.name}`
        }],
        "m1_metrics.json": [{
            yLabel: "TRAIN_LOSS",
            metrics: [{
                seriesLabel: "KCL",
                data: []
            }, {
                seriesLabel: "BDMS",
                data: []
            }]
        }],
        "m1_files.json": [{
            id: FILE_ID,
            name: "models.py",
            modelId: MODEL_ID
        }],
        "m1_config.json": { job_type: "standard" }
    };
}

describe("scrubFiles", () => {
    const out = scrubFiles(rawFiles(), TRUST_MAP) as Record<string, Json>;
    const text = JSON.stringify(out);
    const [gstt, kch] = ROSTER.sites;

    it("maps every participant onto its roster site, everywhere", () => {
        expect(text).not.toMatch(/Bangkok|King's College London|\bBDMS\b|\bKCL\b/);
        expect(out["project.json"].approvedTrusts.map((t: Json) => [t.id, t.name, t.code])).toEqual([
            [gstt.id, gstt.name, gstt.code],
            [kch.id, kch.name, kch.code]
        ]);
        expect(out["m1_metrics.json"][0].metrics.map((s: Json) => s.seriesLabel).sort()).toEqual(["GSTT", "KCH"]);
        expect(out["m1_logs.json"][0].log).toBe(`Selected trusts for training: ${kch.name}, ${gstt.name}`);
    });

    it("drops a non-participant from every list and map, including suppressed and errored trusts", () => {
        expect(text).not.toContain(OTHER.id);
        expect(text).not.toMatch(/AI Centre Private|AICP/);
        expect(out["cohort.json"].trustSuppressed).toEqual([]);
        expect(out["cohort.json"].trustErrors).toEqual({});
        expect(out["project.json"].query.queriedTrustIds.sort()).toEqual([gstt.id, kch.id].sort());
    });

    it("recomputes recordCount and the project's totalCohort from the remaining sites", () => {
        expect(out["cohort.json"].recordCount).toBe(1100);
        expect(out["project.json"].query.totalCohort).toBe(1100);
    });

    it("rewrites every identity to the demo user, by name where the field is a name", () => {
        const project = out["project.json"];
        const { id, email, name, organisation } = ROSTER.user;
        expect(project.ownerId).toBe(id);
        expect(project.ownerEmail).toBe(email);
        expect(project.users).toEqual([{
            id,
            email,
            name,
            organisation,
            isDisabled: false
        }]);
        expect(project.userCount).toBe(1);
        expect(project.query.createdBy).toBe(name);
        expect(out["models.json"].data[0].owner_id).toBe(id);
    });

    it("re-keys every captured id to a stable demo id, keeping roster and demo-user ids", () => {
        for (const id of [PROJECT_ID, MODEL_ID, QUERY_ID, FILE_ID, ADMIN.id]) {
            expect(text).not.toContain(id);
        }
        expect(out["project.json"].id).toBe(demoId(PROJECT_ID));
        expect(out["m1_step.json"].modelId).toBe(demoId(MODEL_ID));
        expect(out["m1_step.json"].projectId).toBe(demoId(PROJECT_ID));
        expect(out["models.json"].data[0].id).toBe(demoId(MODEL_ID));
        expect(demoId(gstt.id)).toBe(gstt.id);
        expect(demoId(ROSTER.user.id)).toBe(ROSTER.user.id);
        expect(demoId(PROJECT_ID)).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-8[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    });

    it("strips the licence header from every query", () => {
        expect(out["project.json"].query.query).toBe("WITH project_images AS (SELECT 1)");
        expect(out["m1_step.json"].query.query).toBe("SELECT 1");
    });

    it("leaves captured timestamps alone", () => {
        expect(out["project.json"].creationtimestamp).toBe("2026-08-21T17:10:17.523Z");
    });

    it("is idempotent: scrubbing scrubbed files changes nothing", () => {
        expect(scrubFiles(clone(out), TRUST_MAP)).toEqual(out);
    });

    it.each([
        ["an extra approved trust", (f: Json) => f["project.json"].approvedTrusts.push({
            ...STRAY,
            status: "APPROVED"
        })],
        ["a model's trust list", (f: Json) => f["m1_step.json"].trusts.push(STRAY)],
        ["a scalar trustId", (f: Json) => { f["image_status.json"][0].trustId = STRAY.id; }],
        ["a lone trustName", (f: Json) => { f["image_status.json"][0].trustName = STRAY.name; }],
        ["a suppressed trust", (f: Json) => f["cohort.json"].trustSuppressed.push(STRAY.id)],
        ["an errored trust", (f: Json) => { f["cohort.json"].trustErrors[STRAY.id] = "boom"; }],
        ["a record count", (f: Json) => { f["cohort.json"].trustRecordCounts[STRAY.id] = 1; }]
    ])("throws on an unmapped trust in %s", (_label, inject) => {
        const files = rawFiles();
        inject(files);
        expect(() => scrubFiles(files, TRUST_MAP)).toThrow(/unmapped trust/);
    });
});

describe("assertClean", () => {
    it.each([
        ["a production trust name", { n: "Bangkok Dusit Medical Services" }, /Bangkok/],
        ["a production code", { code: "KCL" }, /KCL/],
        ["a URL", { u: "https://example.org/x" }, /URL/],
        ["an S3 URL", { u: "s3://bucket/key" }, /URL/],
        ["a presigned signature", { q: "X-Amz-Signature=abc" }, /X-Amz-/],
        ["an IPv4 address", { h: "10.0.4.20" }, /IPv4/],
        ["a foreign email", { e: "someone@nhs.net" }, /someone@nhs\.net/],
        ["a captured trust id", { t: PROD_A.id }, /f43abcd0/]
    ])("refuses %s", (_label, payload, message) => {
        expect(() => assertClean(payload, "t", [PROD_A.id])).toThrow(message);
    });

    it("accepts DICOM UIDs, the demo email and demo ids", () => {
        expect(() => assertClean({
            uid: "1.2.826.0.1.3680043.2.1125.1206566393649540309891485253789764977",
            email: ROSTER.user.email,
            id: demoId(PROJECT_ID)
        }, "t")).not.toThrow();
    });
});

describe("checkFixtures", () => {
    it("finds every committed fixture file clean", () => {
        const result = checkFixtures();
        expect(result.problems).toEqual([]);
        expect(result.checked).toBeGreaterThan(30);
    });
});

describe("checkDist", () => {
    let dist: string;
    beforeEach(() => {
        dist = mkdtempSync(path.join(tmpdir(), "demo-dist-"));
    });
    afterEach(() => rmSync(dist, {
        recursive: true,
        force: true
    }));

    const write = (rel: string, body: string) => {
        mkdirSync(path.dirname(path.join(dist, rel)), { recursive: true });
        writeFileSync(path.join(dist, rel), body);
    };

    it("passes a bundle carrying only demo-world data", () => {
        write("static/demo-server.js", `const s="${ROSTER.sites[0].name}";const e="${ROSTER.user.email}";`);
        expect(checkDist(dist).problems).toEqual([]);
    });

    it("fails a bundle carrying a production name, a captured id or a real email", () => {
        write("static/a.js", "x=\"Bangkok Dusit Medical Services\"");
        write("static/b.js", `y="${PROD_A.id}"`);
        write("static/c.js", "z=\"someone@kcl.ac.uk\"");
        expect(checkDist(dist, [PROD_A.id]).problems.map(p => p.file).sort()).toEqual(["static/a.js", "static/b.js", "static/c.js"]);
    });

    it("ignores the licence header's copyright line, which names the copyright holders", () => {
        write("index.html", "<!--\n    Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London\n-->");
        expect(checkDist(dist).problems).toEqual([]);
    });

    it("still flags that name anywhere else in the same file", () => {
        write("index.html", "<!--\n    Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London\n-->"
            + "<p>Site: King's College London</p>");
        expect(checkDist(dist).problems.map(p => p.file)).toEqual(["index.html"]);
    });

    it("allows the app's own support address, which every build carries on its 404 page", () => {
        write("static/not-found.js", "a.href=\"mailto:aicentreflip@gmail.com\"");
        expect(checkDist(dist).problems).toEqual([]);
    });

    it("fails rather than passing vacuously on an empty build", () => {
        expect(() => checkDist(dist)).toThrow(/no scannable/);
    });
});
