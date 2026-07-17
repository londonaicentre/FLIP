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
 * Serves the REAL platform register of the Ark+ chest X-ray federated
 * fine-tuning experiment — every payload under ./demo/data/ was captured
 * verbatim from the production API (see ./demo/ark-plus-register.ts for the
 * capture provenance and the two sanitisation rules applied).
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
import cohort from "./demo/data/cohort.json";
import filesGet from "./demo/data/files_get.json";
import filesList from "./demo/data/files_list.json";
import flResults from "./demo/data/fl_results.json";
import imageStatus from "./demo/data/image_status.json";
import jobTypes from "./demo/data/job_types.json";
import logs from "./demo/data/logs.json";
import metrics from "./demo/data/metrics.json";
import modelConfig from "./demo/data/model_config.json";
import project from "./demo/data/project.json";
import projectModels from "./demo/data/project_models.json";
import siteDetails from "./demo/data/site_details.json";
import stepModel from "./demo/data/step_model.json";
import trustHealth from "./demo/data/trust_health.json";
import trusts from "./demo/data/trusts.json";

const BASE = "/api";

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

            // ---- Project ------------------------------------------------------
            this.get(`${BASE}/projects`, () => new Response(200, undefined, {
                page: 1,
                pageSize: 20,
                totalPages: 1,
                totalRecords: 1,
                data: [project]
            }));
            this.get(`${BASE}/projects/:projectId`, () => new Response(200, undefined, project));
            this.get(`${BASE}/projects/:projectId/models`, () => new Response(200, undefined, projectModels));
            this.get(`${BASE}/projects/:projectId/image/status`, () => new Response(200, undefined, imageStatus));

            // ---- Cohort query results (the real OMOP counts) -------------------
            this.get(`${BASE}/cohort/:queryId`, () => new Response(200, undefined, cohort));

            // ---- Model dashboard ----------------------------------------------
            // POST / not GET: mirrors the real API (retrieve_model_step_function),
            // which the UI calls on every model-page load. Read-only in effect.
            this.post(`${BASE}/step/model/:modelId`, () => new Response(200, undefined, stepModel));
            this.get(`${BASE}/model/job-types`, () => new Response(200, undefined, jobTypes));
            this.get(`${BASE}/model/:modelId/metrics`, () => new Response(200, undefined, metrics));
            this.get(`${BASE}/model/:modelId/logs`, () => new Response(200, undefined, logs));

            // ---- Model files ----------------------------------------------------
            this.get(`${BASE}/files/model/:modelId/files/list`, () => new Response(200, undefined, filesList));
            this.get(`${BASE}/files/model/:modelId/get/files`, () => new Response(200, undefined, filesGet));
            // Result-bundle links are inert relative paths (never presigned URLs).
            this.get(`${BASE}/files/model/:modelId/fl/results`, () => new Response(200, undefined, flResults));

            // Single-file download used for job-type resolution: config.json is the
            // real deployed training config (byte-equal to the S3 object). Anything
            // else 404s — the demo ships no other file bodies.
            this.get(`${BASE}/files/model/:modelId/:fileName`, (_schema, request) => {
                if (request.params.fileName === "config.json") {
                    return new Response(200, { "Content-Type": "application/json" }, JSON.stringify(modelConfig));
                }

                return new Response(404);
            });

            // NO passthrough(): any unhandled request stays inside Mirage and never
            // reaches the network. This is deliberate — do not add passthrough here.
        }
    });
}
