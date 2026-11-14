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
 * Offline MirageJS server for the public demo build (FLIP#1746; `--mode demo`).
 *
 * Every API call the UI makes is answered here from the registered projects
 * (mocks/demo/registry.ts), whose fixtures are already on the demo world on disk
 * (scripts/demo-fixtures.mjs). There is no passthrough: a published demo bundle
 * cannot reach a real backend, and nothing it serves can be downloaded.
 */

import { createServer, Response, Server } from "miragejs";

import { projectHasImaging } from "@/partials/projects/projectType";

import filesList from "./demo/estate/files_list.json";
import jobTypes from "./demo/estate/job_types.json";
import siteDetails from "./demo/estate/site_details.json";
import { DEMO_PROJECTS, type DemoProject } from "./demo/registry";
import { DEMO_NET_NAME, DEMO_SITES, DEMO_USER } from "./demo/roster";

const BASE = "/api";

type Project = {
    id: string;
    name: string;
    description?: string;
    has_imaging?: boolean;
    query: { id: string };
    approvedTrusts: { id: string }[];
};
type Models = { data: ({ name?: unknown; status?: unknown; owner_id?: unknown } & Record<string, unknown>)[] };

export { DEMO_PROJECTS };

interface Tables {
    projects: readonly DemoProject[];
    projectsById: Record<string, unknown>;
    modelsByProject: Record<string, unknown>;
    imageStatusByProject: Record<string, unknown>;
    cohortByQuery: Record<string, unknown>;
    snapshotsByProject: Record<string, unknown>;
    stepByModel: Record<string, unknown>;
    metricsByModel: Record<string, unknown>;
    logsByModel: Record<string, unknown>;
    filesByModel: Record<string, unknown>;
    configByModel: Record<string, unknown>;
    projectCount: (siteId: string) => number;
}

type Step = { creationTimestamp?: string };
/** Projects approving a site: a site's `project_count` describes the exhibit, not the platform. */
export const projectCountFor = (siteId: string, projects: readonly DemoProject[]): number =>
    projects.filter(demo => (demo.project as Project).approvedTrusts.some(trust => trust.id === siteId)).length;

const buildTables = (projects: readonly DemoProject[]): Tables => {
    const tables: Tables = {
        projects,
        projectsById: {},
        modelsByProject: {},
        imageStatusByProject: {},
        cohortByQuery: {},
        snapshotsByProject: {},
        stepByModel: {},
        metricsByModel: {},
        logsByModel: {},
        filesByModel: {},
        configByModel: {},
        projectCount: siteId => projectCountFor(siteId, projects)
    };
    for (const demo of projects) {
        const project = demo.project as Project;
        tables.projectsById[project.id] = demo.project;
        tables.modelsByProject[project.id] = demo.models;
        tables.cohortByQuery[project.query.id] = demo.cohort;
        tables.snapshotsByProject[project.id] = demo.cohortSnapshots;
        if (demo.imageStatus !== undefined) tables.imageStatusByProject[project.id] = demo.imageStatus;
        for (const [modelId, fixtures] of Object.entries(demo.modelFixtures)) {
            tables.stepByModel[modelId] = fixtures.step;
            tables.metricsByModel[modelId] = fixtures.metrics;
            tables.logsByModel[modelId] = fixtures.logs;
            tables.filesByModel[modelId] = fixtures.files;
            tables.configByModel[modelId] = fixtures.config;
        }
    }

    return tables;
};

const byParam = (table: Record<string, unknown>, key?: string): Response => {
    const hit = key !== undefined ? table[key] : undefined;

    return hit === undefined ? new Response(404) : new Response(200, undefined, hit as never);
};

/**
 * The roster with every `last_heartbeat` at the moment of the request.
 *
 * Connection Status derives its state from `Date.now() - last_heartbeat`
 * (see utils/connection-health.ts), so serving a captured timestamp verbatim
 * made the public exhibit report the whole federation OFFLINE with "N incidents
 * need attention" — worsening by a day for every day the snapshot stayed up,
 * and contradicting /trust/health, which the app header reads as online
 * (FLIP#794 review). Only the clock-relative field is generated, because it is
 * the only field whose meaning depends on when it is read. `project_count` is
 * the number of registered projects approving the site.
 */
const liveTrusts = (tables: Tables): unknown => {
    const now = new Date().toISOString();

    return DEMO_SITES.map(site => ({
        ...site,
        last_heartbeat: now,
        project_count: tables.projectCount(site.id)
    }));
};

const trustHealth = (): unknown => DEMO_SITES.map(site => ({
    trustId: site.id,
    trustName: site.name,
    online: true
}));

// The paginated envelope, holding everything on one page.
const onePage = (data: unknown[]): Record<string, unknown> => ({
    page: 1,
    pageSize: 20,
    totalPages: 1,
    totalRecords: data.length,
    data
});

// The Projects list's Type filter and search, as the hub applies them: an unknown type means every
// type, an absent has_imaging means imaging, and search matches the name or description.
const allProjects = (tables: Tables, query: Record<string, unknown>): unknown => {
    const type = query.projectType;
    const search = String(query.search ?? "").toLowerCase();
    const data = tables.projects
        .map(demo => demo.project as Project)
        .filter(project => (type !== "imaging" && type !== "omop_only")
            || projectHasImaging(project) === (type === "imaging"))
        .filter(project => !search || `${project.name} ${project.description ?? ""}`.toLowerCase().includes(search));

    return onePage(data);
};

/**
 * The estate-wide Models list, derived from the per-project model payloads
 * rather than captured separately — one register, one source of truth.
 *
 * Models is a `canAccess: true` top-level nav item, so an unauthenticated
 * visitor lands on it directly; with no route registered it rendered the
 * generic "Something went wrong" error over a permanent spinner on the public
 * exhibit (FLIP#794 review). The shape is IModelsPage (services/model-service.ts):
 * a paginated IModelSummary list plus the per-status totals the status filter sums.
 * Like the real route, `search` and `project` narrow both the list and the counts,
 * while `status` narrows only the list, so the status filter keeps its counts when one is selected.
 */
const allModels = (tables: Tables, query: Record<string, unknown>): unknown => {
    const projectId = query.project as string | undefined;
    const search = String(query.search ?? "").toLowerCase();
    const statuses = String(query.status ?? "").split(",").filter(Boolean);
    const runTrusts = DEMO_SITES.map(({ id, name, code }) => ({
        id,
        name,
        code
    }));
    const data = tables.projects
        .filter(demo => !projectId || (demo.project as Project).id === projectId)
        .flatMap(demo => {
            const project = demo.project as Project;

            return (demo.models as Models).data.map(model => ({
                ...model,
                projectId: project.id,
                projectName: project.name,
                ownerId: model.owner_id,
                ownerName: DEMO_USER.name,
                trusts: runTrusts,
                // The hub sends it; the captured model rows don't, so read it off the step.
                creationTimestamp: (tables.stepByModel[String(model.id)] as Step | undefined)?.creationTimestamp
            }));
        })
        .filter(model => !search
            || [model.name, model.projectName].some(s => String(s).toLowerCase().includes(search)))
        // Newest first, as the hub orders the page.
        .sort((a, b) => String(b.creationTimestamp ?? "").localeCompare(String(a.creationTimestamp ?? "")));
    const statusCounts = data.reduce<Record<string, number>>((counts, model) => {
        const status = String(model.status);

        return {
            ...counts,
            [status]: (counts[status] ?? 0) + 1
        };
    }, {});
    const listed = statuses.length ? data.filter(model => statuses.includes(String(model.status))) : data;

    return {
        ...onePage(listed),
        statusCounts
    };
};

/**
 * FL network status for the Connection Status page's nets card.
 *
 * Derived from the roster so the client names match the sites shown
 * everywhere else. Without these routes the card span never resolved and sat on
 * its spinner for every visitor (FLIP#794 review). `lastConnected` is
 * generated at request time for the same reason as `liveTrusts` above.
 */
const flNets = (): unknown => [{
    name: DEMO_NET_NAME,
    clients: DEMO_SITES.map(({ code }) => ({
        name: code,
        online: true,
        lastConnected: new Date().toUTCString()
    }))
}];

/**
 * @param options.timing Per-response delay in ms. Defaults to Mirage's 400ms
 *   "production" latency, which is deliberate in the browser — it keeps the
 *   demo's loading states visible rather than snapping instantly, so the
 *   exhibit reads like the real platform. Tests pass 0: at ~60 requests a run
 *   the default costs ~20s of CI for no signal.
 * @param options.projects The registry to serve; defaults to every registered
 *   project. Tests pass a subset to exercise one shape (e.g. a tabular project).
 */
export function makeDemoServer(options: { timing?: number; projects?: readonly DemoProject[] } = {}): Server {
    const tables = buildTables(options.projects ?? DEMO_PROJECTS);
    const server = createServer({
        environment: "production",
        timing: options.timing ?? 400,
        routes() {
            this.get(`${BASE}/trust/health`, () => new Response(200, undefined, trustHealth() as never));
            this.get(`${BASE}/trust`, () => new Response(200, undefined, liveTrusts(tables) as never));
            this.get(`${BASE}/fl/status`, () => new Response(200, undefined, flNets() as never));
            this.get(`${BASE}/fl/:netName/status`, () => new Response(200, undefined, flNets() as never));
            this.get(`${BASE}/site/details`, () => new Response(200, undefined, siteDetails));
            this.get(`${BASE}/users/me/mfa/status`, () => new Response(200, undefined, {
                enabled: true,
                required: false
            }));
            this.get(`${BASE}/users/:email`, () => new Response(200, undefined, DEMO_USER));
            this.get(`${BASE}/users/:userId/permissions`, () => new Response(200, undefined, { permissions: [] }));

            this.get(`${BASE}/projects`, (_schema, request) =>
                new Response(200, undefined, allProjects(tables, request.queryParams ?? {}) as never));
            this.get(`${BASE}/projects/:projectId`, (_schema, request) =>
                byParam(tables.projectsById, request.params.projectId));
            this.get(`${BASE}/projects/:projectId/models`, (_schema, request) =>
                byParam(tables.modelsByProject, request.params.projectId));
            this.get(`${BASE}/projects/:projectId/cohort-snapshots`, (_schema, request) =>
                byParam(tables.snapshotsByProject, request.params.projectId));
            this.get(`${BASE}/projects/:projectId/image/status`, (_schema, request) =>
                byParam(tables.imageStatusByProject, request.params.projectId));
            this.get(`${BASE}/cohort/:queryId`, (_schema, request) =>
                byParam(tables.cohortByQuery, request.params.queryId));

            this.get(`${BASE}/models`, (_schema, request) =>
                new Response(200, undefined, allModels(tables, request.queryParams ?? {}) as never));
            this.get(`${BASE}/models/projects`, () => new Response(200, undefined, tables.projects.map(demo => ({
                id: (demo.project as Project).id,
                name: (demo.project as Project).name,
                status: "APPROVED"
            })) as never));

            this.post(`${BASE}/step/model/:modelId`, (_schema, request) =>
                byParam(tables.stepByModel, request.params.modelId));
            this.get(`${BASE}/model/job-types`, () => new Response(200, undefined, jobTypes));
            this.get(`${BASE}/model/:modelId/metrics`, (_schema, request) =>
                byParam(tables.metricsByModel, request.params.modelId));
            this.get(`${BASE}/model/:modelId/logs`, (_schema, request) =>
                byParam(tables.logsByModel, request.params.modelId));

            this.get(`${BASE}/files/model/:modelId/files/list`, () => new Response(200, undefined, filesList));
            this.get(`${BASE}/files/model/:modelId/get/files`, (_schema, request) =>
                byParam(tables.filesByModel, request.params.modelId));
            // Browse-only: no run has downloadable results in the demo.
            this.get(`${BASE}/files/model/:modelId/fl/results`, () => new Response(200, undefined, [] as never));
            // config.json is the one file body served: the job-type resolver
            // reads it. Every other file is metadata only — browse, not download.
            this.get(`${BASE}/files/model/:modelId/:fileName`, (_schema, request) => {
                const config = tables.configByModel[request.params.modelId ?? ""];
                if (request.params.fileName === "config.json" && config !== undefined) {
                    return new Response(200, { "Content-Type": "application/json" }, JSON.stringify(config));
                }

                return new Response(404);
            });
        }
    });
    server.logging = false;

    return server;
}
