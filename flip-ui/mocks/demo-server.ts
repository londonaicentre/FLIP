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

/**
 * The recorded roster with every `last_heartbeat` moved up to the moment of the
 * request.
 *
 * Connection Status derives its state from `Date.now() - last_heartbeat`
 * (see utils/connection-health.ts), so serving the captured timestamps verbatim
 * made the public exhibit report the whole federation OFFLINE with "N incidents
 * need attention" — worsening by a day for every day the snapshot stayed up,
 * and contradicting trust_health.json, which the app header reads as online
 * (FLIP#794 review). Every other field stays exactly as captured; only the
 * clock-relative one is re-based, because it is the only field whose meaning
 * depends on when it is read.
 */
const liveTrusts = (): unknown => {
    const now = Date.now();

    // Keep the recorded spread between trusts (a few hundred ms) rather than
    // stamping them identically — the radial view animates off these deltas.
    const recorded = trusts as { last_heartbeat: string }[];
    const newest = Math.max(...recorded.map(t => new Date(t.last_heartbeat).getTime()));

    return recorded.map(trust => ({
        ...trust,
        last_heartbeat: new Date(now - (newest - new Date(trust.last_heartbeat).getTime())).toISOString()
    }));
};

/**
 * The estate-wide Models list, derived from the two per-project model payloads
 * rather than captured separately — one register, one source of truth.
 *
 * Models is a `canAccess: true` top-level nav item, so an unauthenticated
 * visitor lands on it directly; with no route registered it rendered the
 * generic "Something went wrong" error over a permanent spinner on the public
 * exhibit (FLIP#794 review). The shape is IModelsPage (services/model-service.ts):
 * a paginated IModelSummary list plus the per-status totals the filter tiles sum.
 */
const allModels = (): unknown => {
    const rows = [
        ...(projectModelsP1 as { data: Record<string, unknown>[] }).data.map(model => ({
            model,
            project: projectP1 as { id: string; name: string }
        })),
        ...(projectModelsP2 as { data: Record<string, unknown>[] }).data.map(model => ({
            model,
            project: projectP2 as { id: string; name: string }
        }))
    ];

    // Every recorded run finished, so the trust list is the project's own — the
    // real endpoint returns [] only for models that never reached dispatch.
    const runTrusts = (trusts as { id: string; name: string; code: string }[])
        .map(({ id, name, code }) => ({ id, name, code }));

    const data: Record<string, unknown>[] = rows.map(({ model, project }) => ({
        ...model,
        projectId: project.id,
        projectName: project.name,
        ownerId: model.owner_id,
        ownerName: DEMO_USER.name,
        trusts: runTrusts
    }));

    const statusCounts = data.reduce<Record<string, number>>((counts, model) => {
        const status = String(model.status);

        return { ...counts, [status]: (counts[status] ?? 0) + 1 };
    }, {});

    return {
        page: 1,
        pageSize: 20,
        totalPages: 1,
        totalRecords: data.length,
        data,
        statusCounts
    };
};

/**
 * FL network status for the Connection Status page's nets card.
 *
 * Derived from the recorded roster so the client names match the trusts shown
 * everywhere else. Without these routes the card span never resolved and sat on
 * its spinner for every visitor (FLIP#794 review). `lastConnected` is re-based
 * to now for the same reason as `liveTrusts` above.
 */
const flNets = (): unknown => {
    const clients = (trusts as { code: string }[]).map(({ code }) => ({
        name: code,
        online: true,
        lastConnected: new Date().toUTCString()
    }));

    return [{ name: "net-1", clients }];
};

/**
 * @param options.timing Per-response delay in ms. Defaults to Mirage's 400ms
 *   "production" latency, which is deliberate in the browser — it keeps the
 *   demo's loading states visible rather than snapping instantly, so the
 *   exhibit reads like the real platform. Tests pass 0: at ~60 requests a run
 *   the default costs ~20s of CI for no signal.
 */
export function makeDemoServer(options: { timing?: number } = {}): Server {
    const server = createServer({
        environment: "production",
        timing: options.timing ?? 400,

        routes() {
            // ---- App-shell / site chrome --------------------------------------
            this.get(`${BASE}/trust/health`, () => new Response(200, undefined, trustHealth));
            this.get(`${BASE}/trust`, () => new Response(200, undefined, liveTrusts() as never));

            // ---- FL networks (Connection Status nets card) ---------------------
            this.get(`${BASE}/fl/status`, () => new Response(200, undefined, flNets() as never));
            this.get(`${BASE}/fl/:netName/status`, () => new Response(200, undefined, flNets() as never));
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

            // ---- Estate-wide Models list --------------------------------------
            // Registered before /model/:modelId/* so the literal path wins.
            this.get(`${BASE}/models`, () => new Response(200, undefined, allModels() as never));

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

    // Silence Mirage's request logging. Under `environment: "production"` it
    // prints every intercepted request AND its full response body to the
    // console, so on a public exhibit the browser console continuously dumps
    // the register — the internal OMOP cohort SQL included — to anyone with
    // devtools open (FLIP#794 review).
    //
    // This MUST be an assignment on the instance, not `logging: false` in the
    // config object above: miragejs 0.1.48 has its ternary branches swapped
    // when it reads the option (dist/mirage-esm.js, `Server.prototype.config`):
    //
    //     this.logging = _config.logging !== undefined ? this.logging : undefined;
    //
    // Passing the option selects `this.logging`, which is still unset at that
    // point, so the value is discarded either way and `shouldLog()` falls back
    // to `!this.isTest()` — true here. Assigning afterwards works because
    // `shouldLog()` reads the property at call time. Do not "tidy" this back
    // into the config object; the egress spec pins the behaviour.
    server.logging = false;

    return server;
}
