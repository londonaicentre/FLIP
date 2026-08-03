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
import { afterEach, beforeEach, describe, expect, it } from "vitest";

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
        server = makeDemoServer();
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
});
