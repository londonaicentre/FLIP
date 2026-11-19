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
 * The public demo's fixture tool (FLIP#1746). The demo bundle serves its
 * fixtures as they are on disk, so the files themselves must hold only the
 * demo world: this tool scrubs a capture IN PLACE and checks both the files and
 * a built demo bundle. Nothing here is imported by the app, so its denylist and
 * the captured ids it reads never reach a browser.
 *
 *   node scripts/demo-fixtures.mjs scrub [slug ...]   # rewrite projects/<slug>/data/*.json (all by default)
 *   node scripts/demo-fixtures.mjs check [--dist dir] # fixtures on disk, and optionally a demo build
 *
 * Each project directory carries a capture.json beside its data/: the run it came
 * from and its trustMap (captured trust id -> roster site id, or null for a trust
 * that only answered the cohort query). Rules, applied to every file:
 *   1. every trust reference is mapped through trustMap; a captured trust missing
 *      from it is an error, a null one is dropped from every list and map;
 *   2. counts describing the platform are recomputed (recordCount, totalCohort,
 *      userCount);
 *   3. every identity becomes the demo user (by name where the field is a name);
 *   4. every other captured UUID is re-keyed to a stable demo id (UUIDv8);
 *   5. queries lose the licence header;
 *   6. assertClean refuses any production name, captured id, URL, IPv4 address,
 *      presigned marker or foreign e-mail in the result.
 * Scrubbing scrubbed files changes nothing, so re-running is safe.
 */

import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const UI_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DEMO_DIR = join(UI_ROOT, "mocks/demo");

export const ROSTER = JSON.parse(readFileSync(join(DEMO_DIR, "roster.json"), "utf8"));

/** Production identities: never in a fixture, never in a built bundle. */
export const DENIED_NAMES = [
    "Bangkok Dusit", "BDMS", "King's College London", "KCL", "AI Centre Private", "AICP",
    "K8s Staging Trust", "K8S1", "AZ1"
];
/** Estate addresses: never in a fixture. Not checked in the bundle, where library code legitimately carries them. */
export const DENIED_ADDRESSES = [
    "amazonaws.com", "cloudfront.net", "arn:aws", "/ark_demo/", "localhost", "X-Amz-", "Signature="
];
export const DENIED_STRINGS = [...DENIED_NAMES, ...DENIED_ADDRESSES];

const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;
const URL_SCHEME = /\b(?:https?|s3):\/\//i;
const IPV4 = /(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])/;
const EMAIL = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;
const LICENCE_HEADER = /^\s*(?:--[^\n]*\n)+?--\s*limitations under the License\.[^\n]*\n\s*/;

const escape = text => text.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");
const bounded = needle => new RegExp(
    `${/^[A-Za-z0-9]/.test(needle) ? "(?<![A-Za-z0-9])" : ""}${escape(needle)}${/[A-Za-z0-9]$/.test(needle) ? "(?![A-Za-z0-9])" : ""}`
);
const isObj = value => typeof value === "object" && value !== null && !Array.isArray(value);
const KEEP_IDS = new Set([...ROSTER.sites.map(site => site.id), ROSTER.user.id]);
const isDemoId = id => id[14] === "8" && "89ab".includes(id[19]);
const NET = /(?<![A-Za-z0-9])net-\d+(?![0-9])/g;

/** A stable demo id for a captured UUID: UUIDv8 from a hash. Roster, demo-user and demo ids map to themselves. */
export function demoId(id) {
    const lower = id.toLowerCase();
    if (KEEP_IDS.has(lower) || lower[14] === "8") return lower;
    const h = createHash("sha256").update(`flip-demo:${lower}`).digest("hex");
    const variant = "89ab"[parseInt(h[16], 16) & 3];

    return `${h.slice(0, 8)}-${h.slice(8, 12)}-8${h.slice(13, 16)}-${variant}${h.slice(17, 20)}-${h.slice(20, 32)}`;
}

/** Throw naming `where` and the first thing a demo payload must never carry. */
export function assertClean(payload, where, extraDenied = []) {
    const text = JSON.stringify(payload) ?? "";
    for (const needle of [...DENIED_STRINGS, ...extraDenied]) {
        if (bounded(needle).test(text)) throw new Error(`${where}: denied string ${JSON.stringify(needle)}`);
    }
    if (URL_SCHEME.test(text)) throw new Error(`${where}: URL in served payload (${text.match(/\b(?:https?|s3):\/\/[^"\s]{0,40}/i)?.[0]})`);
    const ip = text.match(IPV4);
    if (ip) throw new Error(`${where}: IPv4 address ${ip[0]} in served payload`);
    for (const email of text.match(EMAIL) ?? []) {
        if (email !== ROSTER.user.email) throw new Error(`${where}: e-mail ${email} in served payload`);
    }
    // An allowlist, not a denylist: every id served is a roster id, the demo user's or a re-keyed demo id,
    // so a captured project, model or file id that escaped the re-keying fails here whatever it is.
    for (const id of text.match(UUID) ?? []) {
        const lower = id.toLowerCase();
        if (!KEEP_IDS.has(lower) && !isDemoId(lower)) throw new Error(`${where}: id ${id} is not a roster or demo id`);
    }
    const net = (text.match(NET) ?? []).find(name => name !== ROSTER.net);
    if (net) throw new Error(`${where}: FL net ${net} is not the demo world's ${ROSTER.net}`);
}

/**
 * Scrub one project's files ({ "project.json": ..., "m1_step.json": ..., ... }) onto
 * the demo world. Pure: returns new objects.
 */
export function scrubFiles(files, trustMap) {
    const sites = new Map(ROSTER.sites.map(site => [site.id, site]));
    const dropped = new Set(Object.entries(trustMap).filter(([, to]) => to === null).map(([id]) => id));
    const byId = new Map(ROSTER.sites.map(site => [site.id, site]));
    const byName = new Map(ROSTER.sites.map(site => [site.name, site]));
    const byCode = new Map(ROSTER.sites.map(site => [site.code, site]));
    const droppedNames = new Set();

    const learn = ref => {
        if (!isObj(ref) || typeof ref.id !== "string") return;
        if (dropped.has(ref.id)) {
            if (ref.name) droppedNames.add(ref.name);
            if (ref.code) droppedNames.add(ref.code);

            return;
        }
        const target = trustMap[ref.id];
        if (target === undefined) return;
        const site = sites.get(target);
        if (!site) throw new Error(`trustMap maps ${ref.id} to ${target}, which is not a roster site`);
        byId.set(ref.id, site);
        if (ref.name) byName.set(ref.name, site);
        if (ref.code) byCode.set(ref.code, site);
    };
    for (const trust of files["project.json"]?.approvedTrusts ?? []) learn(trust);
    for (const [name, body] of Object.entries(files)) {
        if (/^m\d+_step\.json$/.test(name)) for (const trust of body?.trusts ?? []) learn(trust);
    }
    // Captured trusts named only in the cohort (non-participants) are known by id alone.
    for (const id of Object.keys(trustMap)) if (!dropped.has(id)) learn({ id });

    const mapId = (id, where) => {
        const site = byId.get(id);
        if (!site) throw new Error(`unmapped trust id ${id} in ${where}`);

        return site.id;
    };
    const keepId = (id, where) => (dropped.has(id) ? null : mapId(id, where));

    // Single pass, longest first, so a swapped map cannot rewrite its own output.
    const replacements = new Map([
        ...[...byName].map(([name, site]) => [name, site.name]),
        ...[...byCode].map(([code, site]) => [code, site.code])
    ]);
    const termRe = new RegExp(
        `(?<![A-Za-z0-9])(?:${[...replacements.keys()].sort((a, b) => b.length - a.length).map(escape).join("|")})(?![A-Za-z0-9])`,
        "g"
    );
    const rewriteText = (text, where) => text
        .replace(termRe, term => replacements.get(term))
        .replace(NET, ROSTER.net)
        .replace(UUID, id => {
            const lower = id.toLowerCase();
            if (dropped.has(lower)) throw new Error(`unmapped trust id ${id} (dropped) in text at ${where}`);

            return byId.has(lower) ? byId.get(lower).id : demoId(lower);
        });

    const isDroppedItem = item =>
        (typeof item === "string" && dropped.has(item))
        || (isObj(item) && [item.trustId, item.trust_id, typeof item.code === "string" ? item.id : undefined].some(id => dropped.has(id)));

    const walk = (value, where) => {
        if (Array.isArray(value)) return value.filter(item => !isDroppedItem(item)).map((item, i) => walk(item, `${where}[${i}]`));
        if (typeof value === "string") return rewriteText(value, where);
        if (!isObj(value)) return value;
        const out = {};
        for (const [key, inner] of Object.entries(value)) {
            const here = `${where}.${key}`;
            if ((key === "trustId" || key === "trust_id") && typeof inner === "string") {
                out[key] = mapId(inner, here);
            } else if ((key === "trustName" || key === "trust_name" || key === "trustCode") && typeof inner === "string") {
                const site = byName.get(inner) ?? byCode.get(inner);
                if (!site) throw new Error(`unmapped trust ${JSON.stringify(inner)} at ${here}`);
                out[key] = key === "trustCode" ? site.code : site.name;
            } else if (key === "seriesLabel" && typeof inner === "string") {
                out[key] = byCode.get(inner)?.code ?? byName.get(inner)?.code ?? rewriteText(inner, here);
            } else if ((/TrustIds$/.test(key) || key === "trustSuppressed") && Array.isArray(inner)) {
                out[key] = inner.filter(id => !dropped.has(id)).map((id, i) => mapId(id, `${here}[${i}]`));
            } else if ((key === "trustRecordCounts" || key === "trustErrors") && isObj(inner)) {
                out[key] = Object.fromEntries(Object.entries(inner)
                    .map(([id, v]) => [keepId(id, `${here}.${id}`), key === "trustErrors" ? rewriteText(String(v), here) : v])
                    .filter(([id]) => id !== null));
            } else if (key === "query" && typeof inner === "string") {
                out[key] = rewriteText(inner.replace(LICENCE_HEADER, ""), here);
            } else if (/^(ownerId|owner_id|userId|decidedBy)$/.test(key) && typeof inner === "string") {
                out[key] = ROSTER.user.id;
            } else if (/^(ownerName|decidedByName|createdBy)$/.test(key) && typeof inner === "string") {
                out[key] = ROSTER.user.name;
            } else if (key === "ownerEmail" && typeof inner === "string") {
                out[key] = ROSTER.user.email;
            } else {
                out[rewriteText(key, here)] = walk(inner, here);
            }
        }
        // A trust reference ({id, code, ...}) is rewritten as one unit, and must be known.
        if (typeof out.code === "string" && typeof value.id === "string" && ("name" in value)) {
            const site = byId.get(value.id) ?? byId.get(out.id);
            if (!site) throw new Error(`unmapped trust ${JSON.stringify(value.name)} (${value.id}) at ${where}`);
            Object.assign(out, {
                id: site.id,
                name: site.name,
                code: site.code
            });
        }
        // A user-shaped object is the demo user.
        if ("email" in out) {
            for (const k of ["id", "email", "name", "organisation"]) if (k in out) out[k] = ROSTER.user[k];
        }
        if (isObj(out.trustRecordCounts)) {
            out.recordCount = Object.values(out.trustRecordCounts).reduce((sum, n) => sum + Number(n), 0);
        }
        if (Array.isArray(out.users)) out.userCount = out.users.length || 1;

        return out;
    };

    const result = Object.fromEntries(Object.entries(files).map(([name, body]) => [name, walk(body, name)]));
    const cohort = result["cohort.json"];
    const project = result["project.json"];
    if (cohort && project?.query && typeof cohort.recordCount === "number") project.query.totalCohort = cohort.recordCount;

    const extra = [...Object.keys(trustMap), ...[...droppedNames].filter(n => !byName.has(n) && !byCode.has(n))];
    for (const [name, body] of Object.entries(result)) assertClean(body, name, extra);

    return result;
}

const projectDirs = (demoDir = DEMO_DIR) => {
    const root = join(demoDir, "projects");

    return (existsSync(root) ? readdirSync(root) : [])
        .map(slug => join(root, slug))
        .filter(dir => existsSync(join(dir, "capture.json")));
};

const readCapture = dir => JSON.parse(readFileSync(join(dir, "capture.json"), "utf8"));
const capturedIds = (demoDir = DEMO_DIR) => projectDirs(demoDir)
    .flatMap(dir => Object.keys(readCapture(dir).trustMap ?? {}));
const readJsonDir = dir => Object.fromEntries(readdirSync(dir).filter(f => f.endsWith(".json"))
    .map(f => [f, JSON.parse(readFileSync(join(dir, f), "utf8"))]));

/** Rewrite projects/<slug>/data/*.json in place. Returns the slugs scrubbed. */
export function scrubProjects(slugs = [], demoDir = DEMO_DIR) {
    const dirs = projectDirs(demoDir).filter(dir => !slugs.length || slugs.includes(dir.split("/").pop()));
    for (const dir of dirs) {
        const scrubbed = scrubFiles(readJsonDir(join(dir, "data")), readCapture(dir).trustMap ?? {});
        for (const [name, body] of Object.entries(scrubbed)) {
            // Pretty-printed for review, except a file that would outgrow the repo's
            // 1000 KB large-file hook that way (a 250-round metrics series).
            const pretty = JSON.stringify(body, null, 2);
            writeFileSync(join(dir, "data", name), (pretty.length > 500_000 ? JSON.stringify(body) : pretty) + "\n");
        }
    }

    return dirs.map(dir => dir.split("/").pop());
}

/** Every committed fixture (project data and estate files) must already be clean. */
export function checkFixtures(demoDir = DEMO_DIR) {
    const extra = capturedIds(demoDir);
    const problems = [];
    let checked = 0;
    const dirs = [...projectDirs(demoDir).map(dir => join(dir, "data")), join(demoDir, "estate")].filter(existsSync);
    for (const dir of dirs) {
        for (const [name, body] of Object.entries(readJsonDir(dir))) {
            checked += 1;
            try {
                assertClean(body, relative(UI_ROOT, join(dir, name)), extra);
            } catch (error) {
                problems.push(error.message);
            }
        }
    }

    return {
        problems,
        checked
    };
}

const walkFiles = dir => readdirSync(dir).flatMap(entry => {
    const full = join(dir, entry);

    return statSync(full).isDirectory() ? walkFiles(full) : [full];
});

/**
 * The app's own contact address (the support link on the 404 page): product
 * content in every build, so the bundle check allows it. Fixture data may not
 * carry it — checkFixtures still refuses every e-mail but the demo user's.
 */
const APP_CONTACT_EMAILS = new Set(["aicentreflip@gmail.com"]);

/** The Apache header's copyright line names the copyright holders, not a site: ignore exactly that line. */
const COPYRIGHT_LINE = /Copyright \(c\) \d{4} Guy's and St Thomas' NHS Foundation Trust & King's College London/g;

/**
 * Check a built demo bundle: no production name or code, no captured trust id and
 * no real e-mail but the app's contact address. (URLs and IPs are not checked
 * here: library code legitimately carries both.)
 */
export function checkDist(distDir, extraDenied = capturedIds()) {
    const files = walkFiles(distDir).filter(f => /\.(js|mjs|css|html|json|map|txt)$/i.test(f));
    if (files.length === 0) throw new Error(`no scannable build output under ${distDir}`);
    const problems = [];
    for (const file of files) {
        const text = readFileSync(file, "utf8").replace(COPYRIGHT_LINE, "");
        const realEmail = email => email !== ROSTER.user.email && !APP_CONTACT_EMAILS.has(email)
            && /@(?:gmail|nhs|kcl|aicentre)/i.test(email);
        const hit = [...DENIED_NAMES, ...extraDenied].find(needle => bounded(needle).test(text))
            ?? (text.match(EMAIL) ?? []).find(realEmail);
        if (hit) problems.push({
            file: relative(distDir, file),
            hit
        });
    }

    return {
        problems,
        checked: files.length
    };
}

const USAGE = "usage: node scripts/demo-fixtures.mjs scrub [slug ...] | check [--dist <dir>]";

/**
 * The command line, as a function so it can be tested: returns the exit code.
 * `options.demoDir` points it at another demo directory (tests use a temporary one).
 */
export function runCli(argv, io, { demoDir = DEMO_DIR } = {}) {
    const [command, ...rest] = argv;
    if (command === "scrub") {
        const known = new Set(projectDirs(demoDir).map(dir => dir.split("/").pop()));
        const unknown = rest.filter(slug => !known.has(slug));
        if (unknown.length) {
            io.error(`demo-fixtures: no project ${unknown.join(", ")} under ${relative(UI_ROOT, join(demoDir, "projects"))}; scrubbed nothing`);

            return 1;
        }
        io.log(`demo-fixtures: scrubbed ${scrubProjects(rest, demoDir).join(", ") || "nothing"}`);

        return 0;
    }
    if (command !== "check") {
        io.error(USAGE);

        return 2;
    }
    const fixtures = checkFixtures(demoDir);
    fixtures.problems.forEach(problem => io.error(`  ${problem}`));
    let failed = fixtures.problems.length > 0;
    const distIndex = rest.indexOf("--dist");
    if (distIndex !== -1) {
        const result = checkDist(resolve(UI_ROOT, rest[distIndex + 1] ?? "dist"), capturedIds(demoDir));
        result.problems.forEach(({ file, hit }) => io.error(`  ${file}: ${JSON.stringify(hit)}`));
        failed ||= result.problems.length > 0;
        io.log(`demo-fixtures: ${result.checked} bundle files scanned`);
    }
    io.log(`demo-fixtures: ${fixtures.checked} fixture files checked`);
    if (failed) {
        io.error("demo-fixtures: FAILED — the public demo would publish what is listed above.");

        return 1;
    }

    return 0;
}

const invokedDirectly = process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href;
if (invokedDirectly) {
    process.exit(runCli(process.argv.slice(2), {
        log: msg => console.log(msg),
        error: msg => console.error(msg)
    }));
}
