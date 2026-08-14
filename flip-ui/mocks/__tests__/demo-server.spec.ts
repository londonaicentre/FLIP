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

import { DEMO_USER } from "../demo/ark-plus-register";
import p2Step1 from "../demo/data/p2_m1_step.json";
import p2Step2 from "../demo/data/p2_m2_step.json";
import p2Project from "../demo/data/p2_project.json";
import project from "../demo/data/project.json";
import stepModel from "../demo/data/step_model.json";
import { makeDemoServer } from "../demo-server";

interface IProjectFixture { id: string; query: { id: string } }
interface IStepFixture { modelId: string }

const p1 = project as unknown as IProjectFixture;
const p2 = p2Project as unknown as IProjectFixture;
const p1m1 = stepModel as unknown as IStepFixture;
const p2m1 = p2Step1 as unknown as IStepFixture;
const p2m2 = p2Step2 as unknown as IStepFixture;

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
    });

    it("answers the top-level projects list", async () => {
        const res = await client.get("/projects");
        expect(res.status).toBe(200);
        expect(res.data.data).toHaveLength(2);
    });

    it("answers /model/job-types", async () => {
        const res = await client.get("/model/job-types");
        expect(res.status).toBe(200);
    });

    it.each([p1, p2])("answers project routes for project $id", async (proj) => {
        const routes = [
            `/projects/${proj.id}`,
            `/projects/${proj.id}/models`,
            `/projects/${proj.id}/image/status`,
            `/cohort/${proj.query.id}`
        ];

        for (const path of routes) {
            const res = await client.get(path);
            expect(res.status, path).toBe(200);
        }
    });

    it.each([p1m1, p2m1, p2m2])("answers model dashboard routes for model $modelId", async (model) => {
        const resStep = await client.post(`/step/model/${model.modelId}`);
        expect(resStep.status, "/step/model").toBe(200);

        const getRoutes = [
            `/model/${model.modelId}/metrics`,
            `/model/${model.modelId}/logs`,
            `/files/model/${model.modelId}/files/list`,
            `/files/model/${model.modelId}/get/files`,
            `/files/model/${model.modelId}/fl/results`,
            `/files/model/${model.modelId}/config.json`
        ];

        for (const path of getRoutes) {
            const res = await client.get(path);
            expect(res.status, path).toBe(200);
        }
    });

    // The route-coverage cases above walk the project and model pages. These
    // cover the other two `canAccess: true` top-level nav destinations, which
    // an unauthenticated visitor can reach directly from the header and which
    // shipped unregistered — Models rendered the generic "Something went wrong"
    // error over a permanent spinner for every visitor (FLIP#794 review).
    it("answers the estate-wide Models list with the shape the page reads", async () => {
        const res = await client.get("/models?pageNumber=1&pageSize=20");
        expect(res.status).toBe(200);

        // Three recorded models across the two recorded projects.
        expect(res.data.data).toHaveLength(3);
        expect(res.data.totalRecords).toBe(3);

        // models.vue reads statusCounts to size its filter tiles; a bare
        // paginated list would render every tile as 0.
        expect(res.data.statusCounts).toEqual({ RESULTS_UPLOADED: 3 });

        for (const model of res.data.data) {
            // IModelSummary requires the project join and a status on every row.
            expect(model.projectId, model.id).toBeTruthy();
            expect(model.projectName, model.id).toBeTruthy();
            expect(model.status, model.id).toBeTruthy();
        }
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

        // ConnectionStatus derives OFFLINE/DEGRADED/ONLINE from
        // Date.now() - last_heartbeat. Pinning the captured timestamps made the
        // public exhibit report the whole federation offline, worsening daily
        // (FLIP#794 review). Anything inside a minute is unambiguously "live"
        // and leaves room for a slow CI machine.
        for (const trust of res.data) {
            const age = Date.now() - new Date(trust.last_heartbeat).getTime();
            expect(age, `${trust.code} heartbeat age`).toBeLessThan(60_000);
            expect(age, `${trust.code} heartbeat age`).toBeGreaterThanOrEqual(0);
        }
    });

    it("serves a config.json body the job-type resolver can parse", async () => {
        // Previously asserted only `status === 200`, which is why the response
        // being the config *body* where file-service expected a presigned
        // `{url, fileName}` envelope went unnoticed: every poll tick threw on
        // fetch(undefined) and both evaluation models silently fell back to
        // jobType "standard" (FLIP#794 review).
        const res = await client.get(`/files/model/${p2m1.modelId}/config.json`);
        expect(res.status).toBe(200);

        const config = typeof res.data === "string"
            ? JSON.parse(res.data) as { job_type?: string }
            : res.data as { job_type?: string };

        expect(config.job_type).toBeTruthy();
    });

    // The guarantee the whole design rests on: the published demo bundle cannot
    // reach a real API, database or Cognito, which is what makes it safe to host
    // unauthenticated. It holds only because demo-server.ts registers no
    // passthrough — and nothing failed if someone added one (FLIP#794 review).
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
                "/fl/status", "/model/job-types", `/projects/${p1.id}`, `/cohort/${p1.query.id}`
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
                await client.get(`/projects/${p2.id}`);

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
