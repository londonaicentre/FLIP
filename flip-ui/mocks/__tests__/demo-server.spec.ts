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
 * Route-coverage smoke test for the public Ark+ demo Mirage server.
 *
 * demo-server.ts registers NO passthrough — every request the demo pages
 * make must be answered by a route defined here, or it stays unhandled
 * inside Mirage. Register/page drift (a new UI page calling an endpoint the
 * recorded register never served) would otherwise surface only as a blank
 * panel discovered by a public visitor. This walks every route the project
 * and model pages actually hit, for both recorded projects and all three
 * recorded models, and asserts each one resolves — catching that class of
 * drift at build time instead.
 */

import axios from "axios";
import type { Server } from "miragejs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { assertClean } from "../../scripts/demo-fixtures.mjs";
import { ARKPLUS_FINETUNE } from "../demo/projects/arkplus-finetune/register";
import { type DemoProject } from "../demo/registry";
import { DEMO_SITES, DEMO_USER } from "../demo/roster";
import { DEMO_PROJECTS, makeDemoServer, projectCountFor } from "../demo-server";

interface IProjectFixture {
    id: string;
    name: string;
    approvedTrusts: { id: string; code: string }[];
    query: { id: string };
}
interface IModelsFixture { data: { id: string }[] }

const projectOf = (demo: { project: unknown }): IProjectFixture => demo.project as IProjectFixture;
const modelIdsOf = (demo: { models: unknown }): string[] => (demo.models as IModelsFixture).data.map(m => m.id);
const SITE_IDS = new Set(DEMO_SITES.map(site => site.id));
const SITE_CODES = new Set(DEMO_SITES.map(site => site.code));

describe("makeDemoServer route coverage", () => {
    let server: Server;
    let client: ReturnType<typeof axios.create>;

    beforeEach(() => {
        // timing: 0 — the browser default of 400ms/response is a UX choice for
        // the exhibit, not something this suite should pay ~20s a run for.
        server = makeDemoServer({ timing: 0 });
        // A fresh, minimal axios instance rather than the app's `_http`
        // singleton — that one pulls in the Pinia auth store and Amplify
        // interceptors, which is more than this route-coverage check needs.
        // validateStatus never throws, so an unhandled-route 404/500 shows up
        // as a normal assertion failure with a useful message.
        client = axios.create({
            baseURL: "/api",
            validateStatus: () => true
        });
    });

    afterEach(() => {
        server.shutdown();
    });

    it("answers the app-shell / site-chrome routes", async () => {
        for (const path of ["/trust/health", "/trust", "/site/details", "/users/me/mfa/status"]) {
            const res = await client.get(path);
            expect(res.status, path).toBe(200);
        }
    });

    it("answers the identity routes for the seeded demo user", async () => {
        const resUser = await client.get(`/users/${DEMO_USER.email}`);
        expect(resUser.status).toBe(200);

        // src/demo/bootstrap.ts seeds this exact userId (not DEMO_USER.id) as
        // the signed-in identity — MainLayout resolves permissions from it.
        const resPermissions = await client.get("/users/demo-researcher/permissions");
        expect(resPermissions.status).toBe(200);
        // Neither an admin nor a Trust Admin, so the trust card's kit section stays hidden and its
        // routes, which this server does not serve, are never called (FLIP#1852).
        expect(resPermissions.data).toEqual({ permissions: [] });
    });

    it("lists every registered project, in registry order", async () => {
        const res = await client.get("/projects");
        expect(res.status).toBe(200);
        expect(res.data.totalRecords).toBe(DEMO_PROJECTS.length);
        expect(res.data.data.map((p: { id: string }) => p.id)).toEqual(DEMO_PROJECTS.map(d => projectOf(d).id));
    });

    it("filters the Projects list by type and search", async () => {
        const all = (await client.get("/projects")).data.data as { id: string; name: string; has_imaging?: boolean }[];
        const omop = (await client.get("/projects?projectType=omop_only")).data;
        const imaging = (await client.get("/projects?projectType=imaging")).data;
        expect(omop.data.length).toBeGreaterThan(0);
        expect(omop.data.every((p: { has_imaging?: boolean }) => p.has_imaging === false)).toBe(true);
        expect(imaging.totalRecords + omop.totalRecords).toBe(all.length);

        const name = all[0].name;
        const searched = (await client.get(`/projects?search=${encodeURIComponent(name.toUpperCase())}`)).data;
        expect(searched.data.map((p: { name: string }) => p.name)).toContain(name);
        expect(searched.totalRecords).toBeLessThan(all.length);
    });

    it("answers /model/job-types", async () => {
        const res = await client.get("/model/job-types");
        expect(res.status).toBe(200);
    });

    it.each(DEMO_PROJECTS.map(d => [d.slug, d] as const))("answers project routes for %s", async (_slug, demo) => {
        const proj = projectOf(demo);
        const routes = [
            `/projects/${proj.id}`,
            `/projects/${proj.id}/models`,
            `/projects/${proj.id}/cohort-snapshots`,
            `/cohort/${proj.query.id}`
        ];
        if (demo.imageStatus !== undefined) routes.push(`/projects/${proj.id}/image/status`);

        for (const path of routes) {
            const res = await client.get(path);
            expect(res.status, path).toBe(200);
        }
    });

    it("answers 404 on image status for a tabular project", async () => {
        server.shutdown();
        const { imageStatus: _imageStatus, ...tabular } = ARKPLUS_FINETUNE;
        void _imageStatus;
        server = makeDemoServer({
            timing: 0,
            projects: [tabular as DemoProject]
        });
        const res = await client.get(`/projects/${projectOf(ARKPLUS_FINETUNE).id}/image/status`);
        expect(res.status).toBe(404);
    });

    it.each(DEMO_PROJECTS.flatMap(d => modelIdsOf(d)))("answers model dashboard routes for model %s", async (modelId) => {
        const resStep = await client.post(`/step/model/${modelId}`);
        expect(resStep.status, "/step/model").toBe(200);

        const getRoutes = [
            `/model/${modelId}/metrics`,
            `/model/${modelId}/logs`,
            `/files/model/${modelId}/files/list`,
            `/files/model/${modelId}/get/files`,
            `/files/model/${modelId}/fl/results`,
            `/files/model/${modelId}/config.json`
        ];
        for (const path of getRoutes) {
            const res = await client.get(path);
            expect(res.status, path).toBe(200);
        }
    });

    it("answers the estate-wide Models list with the shape the page reads", async () => {
        const total = DEMO_PROJECTS.reduce((n, d) => n + modelIdsOf(d).length, 0);
        const res = await client.get("/models?pageNumber=1&pageSize=20");
        expect(res.status).toBe(200);
        expect(res.data.data).toHaveLength(total);
        expect(res.data.totalRecords).toBe(total);
        for (const model of res.data.data) {
            expect(model.projectId, model.id).toBeTruthy();
            expect(model.projectName, model.id).toBeTruthy();
            expect(model.status, model.id).toBeTruthy();
        }
    });

    it("carries each model's creation time, newest first", async () => {
        const { data } = (await client.get("/models")).data as { data: { creationTimestamp: string }[] };
        const created = data.map(model => model.creationTimestamp);
        expect(created.every(Boolean)).toBe(true);
        expect(created).toEqual([...created].sort().reverse());
    });

    it("filters the estate-wide Models list by project and sums statusCounts to totalRecords", async () => {
        const twoModels = DEMO_PROJECTS.find(d => modelIdsOf(d).length === 2);
        expect(twoModels, "a project with two models").toBeDefined();
        const res = await client.get(`/models?project=${projectOf(twoModels as (typeof DEMO_PROJECTS)[number]).id}`);
        expect(res.status).toBe(200);
        expect(res.data.totalRecords).toBe(2);
        const counted = Object.values(res.data.statusCounts as Record<string, number>).reduce((a, b) => a + b, 0);
        expect(counted).toBe(res.data.totalRecords);
    });

    it("filters the Models list by status and search, keeping the tile counts status-unfiltered", async () => {
        const all = await client.get("/models");
        const picked = Object.keys(all.data.statusCounts as Record<string, number>)[0];
        const res = await client.get(`/models?status=${picked},ERROR`);
        expect(res.data.data.length).toBeGreaterThan(0);
        expect(res.data.data.every((m: { status: string }) => m.status === picked)).toBe(true);
        expect(res.data.totalRecords).toBe(all.data.statusCounts[picked]);
        expect(res.data.statusCounts).toEqual(all.data.statusCounts);

        const absent = ["ERROR", "RUNNING", "PENDING"].find(s => !(s in all.data.statusCounts));
        const none = await client.get(`/models?status=${absent}`);
        expect(none.data.data).toEqual([]);
        expect(none.data.totalRecords).toBe(0);

        const name = (all.data.data[0] as { name: string }).name;
        const searched = await client.get(`/models?search=${encodeURIComponent(name.toUpperCase())}`);
        expect(searched.data.data.map((m: { name: string }) => m.name)).toContain(name);
        expect(searched.data.totalRecords).toBeLessThan(all.data.totalRecords);
    });

    it("answers the FL network status routes behind the Connection Status nets card", async () => {
        for (const path of ["/fl/status", "/fl/net-1/status"]) {
            const res = await client.get(path);
            expect(res.status, path).toBe(200);
            expect(res.data.length, path).toBeGreaterThan(0);
        }
    });

    it("serves trust heartbeats relative to now, not the capture date", async () => {
        const res = await client.get("/trust");
        expect(res.status).toBe(200);
        for (const trust of res.data) {
            const age = Date.now() - new Date(trust.last_heartbeat).getTime();
            expect(age, `${trust.code} heartbeat age`).toBeLessThan(60_000);
            expect(age, `${trust.code} heartbeat age`).toBeGreaterThanOrEqual(0);
        }
    });

    it("names only roster sites on every estate-wide route", async () => {
        const roster = await client.get("/trust");
        expect(roster.data.map((t: { id: string }) => t.id)).toEqual(DEMO_SITES.map(site => site.id));
        const health = await client.get("/trust/health");
        for (const trust of health.data) {
            expect(SITE_IDS, `/trust/health → ${trust.trustName}`).toContain(trust.trustId);
        }
        const nets = await client.get("/fl/status");
        for (const net of nets.data) {
            for (const netClient of net.clients) {
                expect(SITE_CODES, `/fl/status ${net.name} → ${netClient.name}`).toContain(netClient.name);
            }
        }
    });

    it("counts each site's projects from the register, not the live platform", async () => {
        const res = await client.get("/trust");
        for (const trust of res.data) {
            expect(trust.project_count, `${trust.code} project_count`).toBe(projectCountFor(trust.id, DEMO_PROJECTS));
        }
    });

    it("counts each project's users to match the users it lists", async () => {
        const res = await client.get("/projects");
        for (const proj of res.data.data) {
            expect(proj.userCount, `${proj.name} userCount`).toBe(proj.users.length);
        }
    });

    it("serves a config.json body the job-type resolver can parse", async () => {
        const [modelId] = modelIdsOf(DEMO_PROJECTS[1] as (typeof DEMO_PROJECTS)[number]);
        const res = await client.get(`/files/model/${modelId}/config.json`);
        expect(res.status).toBe(200);
        const config = typeof res.data === "string"
            ? JSON.parse(res.data) as { job_type?: string }
            : res.data as { job_type?: string };
        expect(config.job_type).toBeTruthy();
    });

    it("serves no download URL and no denied string on any route", async () => {
        const gets = ["/trust", "/trust/health", "/fl/status", "/projects", "/models", "/models/projects", "/site/details"];
        const posts: string[] = [];
        for (const demo of DEMO_PROJECTS) {
            const proj = projectOf(demo);
            gets.push(
                `/projects/${proj.id}`,
                `/projects/${proj.id}/models`,
                `/projects/${proj.id}/cohort-snapshots`,
                `/cohort/${proj.query.id}`
            );
            if (demo.imageStatus !== undefined) gets.push(`/projects/${proj.id}/image/status`);
            for (const modelId of modelIdsOf(demo)) {
                posts.push(`/step/model/${modelId}`);
                gets.push(
                    `/model/${modelId}/metrics`,
                    `/model/${modelId}/logs`,
                    `/files/model/${modelId}/files/list`,
                    `/files/model/${modelId}/get/files`,
                    `/files/model/${modelId}/fl/results`,
                    `/files/model/${modelId}/config.json`
                );
            }
        }
        const responses = [
            ...await Promise.all(gets.map(async path => [path, await client.get(path)] as const)),
            ...await Promise.all(posts.map(async path => [path, await client.post(path)] as const))
        ];
        for (const [path, res] of responses) {
            expect(res.status, path).toBe(200);
            expect(() => assertClean(res.data, path), path).not.toThrow();
        }
        for (const demo of DEMO_PROJECTS) {
            for (const modelId of modelIdsOf(demo)) {
                const res = await client.get(`/files/model/${modelId}/fl/results`);
                expect(res.data, modelId).toEqual([]);
            }
        }
    });

    describe("zero egress", () => {
        it("keeps an unregistered request inside Mirage instead of hitting the network", async () => {
            // With a passthrough registered, Pretender hands the request to the
            // native XHR and this rejects with a transport error (or resolves)
            // rather than with Mirage's own "no route defined" refusal.
            await expect(client.get("/not-a-registered-route"))
                .rejects.toThrow(/there was no route defined to handle this request/);
        });

        it("passes no request through to the real network while serving the demo's own routes", async () => {
            const paths = [
                "/trust", "/trust/health", "/site/details", "/projects", "/models",
                "/fl/status", "/model/job-types", `/projects/${projectOf(DEMO_PROJECTS[0] as (typeof DEMO_PROJECTS)[number]).id}`, `/cohort/${projectOf(DEMO_PROJECTS[0] as (typeof DEMO_PROJECTS)[number]).query.id}`
            ];

            for (const path of paths) {
                await client.get(path);
            }

            // Pretender records every request it let escape to the network.
            // Not in miragejs' published Server types, hence the narrowing.
            const pretender = server.pretender as unknown as { passthroughRequests: unknown[] };
            expect(pretender.passthroughRequests).toHaveLength(0);
        });
    });

    // Mirage's `environment: "production"` logs every intercepted request and
    // its FULL response body to the console, which on a public exhibit means
    // the browser console dumps the register — internal OMOP cohort SQL and
    // all — to any visitor with devtools open (FLIP#794 review).
    //
    // Asserted on behaviour, not on the config object: `logging: false` passed
    // to createServer is silently discarded by miragejs 0.1.48 (see the comment
    // in demo-server.ts), so a config-shaped assertion would have passed while
    // the console kept logging.
    describe("console silence", () => {
        it("logs nothing while serving a request", async () => {
            const methods = ["log", "info", "groupCollapsed"] as const;
            const spies = methods.map(name =>
                vi.spyOn(console, name).mockImplementation(() => undefined));

            try {
                await client.get(`/projects/${projectOf(DEMO_PROJECTS[1] as (typeof DEMO_PROJECTS)[number]).id}`);

                methods.forEach((name, i) => {
                    expect(spies[i], `console.${name}`).not.toHaveBeenCalled();
                });
            }
            finally {
                spies.forEach(spy => spy.mockRestore());
            }
        });

        it("reports shouldLog() false, the predicate Mirage actually consults", () => {
            const mirage = server as unknown as { shouldLog: () => boolean };

            expect(mirage.shouldLog()).toBe(false);
        });
    });
});

// Fixture-level invariants over every registered project, as the server
// serves it: a cohort names only roster sites, and names them the way the
// roster does (an aggregate freezes the trust name as of its aggregation day;
// the scrub rewrites it — FLIP#794 review rules 5 and 7).
describe("every registered project names only roster sites, consistently", () => {
    const roster = new Map(DEMO_SITES.map(site => [site.id, site.name]));

    it.each(DEMO_PROJECTS.map(d => [d.slug, d] as const))("%s", (_slug, demo) => {
        const proj = projectOf(demo);
        for (const trust of proj.approvedTrusts) {
            expect(roster.get(trust.id), `approvedTrusts → ${trust.id}`).toBeDefined();
        }
        const cohort = demo.cohort as {
            trustsResults: { results: { trustId: string; trustName: string }[] }[];
            trustRecordCounts: Record<string, number>;
            recordCount: number;
        };
        for (const group of cohort.trustsResults) {
            for (const result of group.results) {
                expect(roster.get(result.trustId), `cohort → ${result.trustName}`).toBe(result.trustName);
            }
        }
        const total = Object.values(cohort.trustRecordCounts).reduce((a, b) => a + b, 0);
        expect(cohort.recordCount).toBe(total);
    });
});
