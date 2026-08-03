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
 * In-browser API server for the public Ark+ demo (VITE_DEMO build).
 *
 * Serves the REAL platform register of the Ark+ chest X-ray experiments —
 * the federated fine-tuning project and its companion federated evaluation
 * project. Every payload under ./demo/data/ was captured verbatim from the
 * production API (see ./demo/ark-plus-register.ts for capture provenance and
 * the sanitisation rules applied).
 *
 * Unlike the development mock server (./server.ts), this one:
 *   1. serves a fixed, read-only record — no create/update/delete/train
 *      routes are defined; and
 *   2. registers NO `passthrough`. Every request is answered locally or fails
 *      inside Mirage, so a published demo bundle can never reach a real API,
 *      database, or Cognito. That is the security guarantee behind hosting it
 *      unauthenticated at /ark_demo.
 *
 * The demo axios client uses baseURL "/api" (pinned in src/main.ts), which is
 * the prefix every route below is registered under.
 */

import { createServer, Response, Server } from "miragejs";

import { DEMO_USER } from "./demo/ark-plus-register";
import cohortP1 from "./demo/data/cohort.json";
import filesGetP1M1 from "./demo/data/files_get.json";
import filesList from "./demo/data/files_list.json";
import flResultsP1M1 from "./demo/data/fl_results.json";
import imageStatusP1 from "./demo/data/image_status.json";
import jobTypes from "./demo/data/job_types.json";
import logsP1M1 from "./demo/data/logs.json";
import metricsP1M1 from "./demo/data/metrics.json";
import modelConfigP1M1 from "./demo/data/model_config.json";
import cohortP2 from "./demo/data/p2_cohort.json";
import imageStatusP2 from "./demo/data/p2_image_status.json";
import configP2M1 from "./demo/data/p2_m1_config.json";
import filesGetP2M1 from "./demo/data/p2_m1_files.json";
import flResultsP2M1 from "./demo/data/p2_m1_flres.json";
import logsP2M1 from "./demo/data/p2_m1_logs.json";
import stepModelP2M1 from "./demo/data/p2_m1_step.json";
import configP2M2 from "./demo/data/p2_m2_config.json";
import filesGetP2M2 from "./demo/data/p2_m2_files.json";
import flResultsP2M2 from "./demo/data/p2_m2_flres.json";
import logsP2M2 from "./demo/data/p2_m2_logs.json";
import stepModelP2M2 from "./demo/data/p2_m2_step.json";
import projectP2 from "./demo/data/p2_project.json";
import projectModelsP2 from "./demo/data/p2_project_models.json";
import projectP1 from "./demo/data/project.json";
import projectModelsP1 from "./demo/data/project_models.json";
import siteDetails from "./demo/data/site_details.json";
import stepModelP1M1 from "./demo/data/step_model.json";
import trustHealth from "./demo/data/trust_health.json";
import trusts from "./demo/data/trusts.json";

const BASE = "/api";

// ---- Register lookup tables -----------------------------------------------
// Two projects: the fine-tuning run (P1) and the federated evaluation run (P2,
// two models: single-model baseline and multi-model comparison).

const P1_ID = (projectP1 as { id: string }).id;
const P2_ID = (projectP2 as { id: string }).id;
const P1_M1 = (stepModelP1M1 as { modelId: string }).modelId;
const P2_M1 = (stepModelP2M1 as { modelId: string }).modelId;
const P2_M2 = (stepModelP2M2 as { modelId: string }).modelId;
const P1_QUERY = (projectP1 as { query: { id: string } }).query.id;
const P2_QUERY = (projectP2 as { query: { id: string } }).query.id;

const projectsById: Record<string, unknown> = {
    [P1_ID]: projectP1,
    [P2_ID]: projectP2
};

const modelsByProject: Record<string, unknown> = {
    [P1_ID]: projectModelsP1,
    [P2_ID]: projectModelsP2
};

const imageStatusByProject: Record<string, unknown> = {
    [P1_ID]: imageStatusP1,
    [P2_ID]: imageStatusP2
};

const cohortByQuery: Record<string, unknown> = {
    [P1_QUERY]: cohortP1,
    [P2_QUERY]: cohortP2
};

const stepByModel: Record<string, unknown> = {
    [P1_M1]: stepModelP1M1,
    [P2_M1]: stepModelP2M1,
    [P2_M2]: stepModelP2M2
};

// Evaluation jobs report no training curves — the real API returns [] there.
const metricsByModel: Record<string, unknown> = {
    [P1_M1]: metricsP1M1,
    [P2_M1]: [],
    [P2_M2]: []
};

const logsByModel: Record<string, unknown> = {
    [P1_M1]: logsP1M1,
    [P2_M1]: logsP2M1,
    [P2_M2]: logsP2M2
};

const filesByModel: Record<string, unknown> = {
    [P1_M1]: filesGetP1M1,
    [P2_M1]: filesGetP2M1,
    [P2_M2]: filesGetP2M2
};

// Result-bundle links point at the CloudFront demo-assets behaviour
// (/ark_demo/assets/*) — never presigned URLs, never raw S3.
const flResultsByModel: Record<string, unknown> = {
    [P1_M1]: flResultsP1M1,
    [P2_M1]: flResultsP2M1,
    [P2_M2]: flResultsP2M2
};

const configByModel: Record<string, unknown> = {
    [P1_M1]: modelConfigP1M1,
    [P2_M1]: configP2M1,
    [P2_M2]: configP2M2
};

const byParam = (table: Record<string, unknown>, key?: string): Response => {
    const hit = key !== undefined ? table[key] : undefined;

    return hit === undefined ? new Response(404) : new Response(200, undefined, hit as never);
};

export function makeDemoServer(): Server {
    return createServer({
        environment: "production",

        routes() {
            // ---- App-shell / site chrome --------------------------------------
            this.get(`${BASE}/trust/health`, () => new Response(200, undefined, trustHealth));
            this.get(`${BASE}/trust`, () => new Response(200, undefined, trusts));
            this.get(`${BASE}/site/details`, () => new Response(200, undefined, siteDetails));
            this.get(`${BASE}/users/me/mfa/status`, () => new Response(200, undefined, {
                enabled: true,
                required: false
            }));

            // ---- Identity (read-only viewer) ----------------------------------
            this.get(`${BASE}/users/:email`, () => new Response(200, undefined, DEMO_USER));
            this.get(`${BASE}/users/:userId/permissions`, () => new Response(200, undefined, { permissions: [] }));

            // ---- Projects -------------------------------------------------------
            this.get(`${BASE}/projects`, () => new Response(200, undefined, {
                page: 1,
                pageSize: 20,
                totalPages: 1,
                totalRecords: 2,
                data: [projectP1, projectP2]
            }));
            this.get(`${BASE}/projects/:projectId`, (_schema, request) =>
                byParam(projectsById, request.params.projectId));
            this.get(`${BASE}/projects/:projectId/models`, (_schema, request) =>
                byParam(modelsByProject, request.params.projectId));
            this.get(`${BASE}/projects/:projectId/image/status`, (_schema, request) =>
                byParam(imageStatusByProject, request.params.projectId));

            // ---- Cohort query results (the real OMOP counts) -------------------
            this.get(`${BASE}/cohort/:queryId`, (_schema, request) =>
                byParam(cohortByQuery, request.params.queryId));

            // ---- Model dashboard ----------------------------------------------
            // POST / not GET: mirrors the real API (retrieve_model_step_function),
            // which the UI calls on every model-page load. Read-only in effect.
            this.post(`${BASE}/step/model/:modelId`, (_schema, request) =>
                byParam(stepByModel, request.params.modelId));
            this.get(`${BASE}/model/job-types`, () => new Response(200, undefined, jobTypes));
            this.get(`${BASE}/model/:modelId/metrics`, (_schema, request) =>
                byParam(metricsByModel, request.params.modelId));
            this.get(`${BASE}/model/:modelId/logs`, (_schema, request) =>
                byParam(logsByModel, request.params.modelId));

            // ---- Model files ----------------------------------------------------
            this.get(`${BASE}/files/model/:modelId/files/list`, () => new Response(200, undefined, filesList));
            this.get(`${BASE}/files/model/:modelId/get/files`, (_schema, request) =>
                byParam(filesByModel, request.params.modelId));
            this.get(`${BASE}/files/model/:modelId/fl/results`, (_schema, request) =>
                byParam(flResultsByModel, request.params.modelId));

            // Single-file download used for job-type resolution: config.json is the
            // real deployed config per model (byte-equal to the S3 object). Anything
            // else 404s — the demo ships no other file bodies.
            this.get(`${BASE}/files/model/:modelId/:fileName`, (_schema, request) => {
                const config = configByModel[request.params.modelId ?? ""];
                if (request.params.fileName === "config.json" && config !== undefined) {
                    return new Response(200, { "Content-Type": "application/json" }, JSON.stringify(config));
                }

                return new Response(404);
            });

            // NO passthrough(): any unhandled request stays inside Mirage and never
            // reaches the network. This is deliberate — do not add passthrough here.
        }
    });
}
