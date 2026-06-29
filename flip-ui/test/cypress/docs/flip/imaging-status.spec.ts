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

// Records docs/source/assets/flip/imaging-status.gif (replaces the former
// hand-captured imaging-status.png). Shows the Imaging Project Status section
// on an approved project: per-trust import progress (retrieved %, successful /
// processing / queued / failed) and the reimport counter. Mirrors the view in
// integration/group-5/view_project.spec.ts ("shows the imaging status").

describe("docs: imaging status", () => {
    it("views the imaging project status overview", () => {
        cy.login();

        const projectId = "6fcbdd40-3675-45c9-899e-1a005e5245ba";
        const uclhId = "a9b4cbe9-5754-4b7c-b2ae-6de495610f1b";
        const kchId = "059943ac-250b-486e-89e0-8d805d10769c";

        cy.intercept("GET", `/projects/${projectId}`, { fixture: "project/getApprovedProject" }).as("getProject");

        // An approved project loads ProjectStatus -> GET /image/status. Provide a
        // mid-import snapshot: both trusts created and retrieving studies, a few
        // failures, reimport counters below the cap (maxReimportCount = 5 from the
        // /site/details stub in globalIntercepts).
        cy.intercept("GET", `**/projects/${projectId}/image/status`, {
            statusCode: 200,
            body: [
                {
                    trustId: uclhId,
                    trustName: "UCLH",
                    projectCreationCompleted: true,
                    importStatus: {
                        successful: 1840,
                        failed: 4,
                        processing: 12,
                        queued: 310,
                        queueFailed: 0
                    },
                    reimportCount: 1
                },
                {
                    trustId: kchId,
                    trustName: "KCH",
                    projectCreationCompleted: true,
                    importStatus: {
                        successful: 1520,
                        failed: 0,
                        processing: 8,
                        queued: 540,
                        queueFailed: 0
                    },
                    reimportCount: 0
                }
            ]
        }).as("getImageStatus");

        // Settle the Models card with a single model so it doesn't spin on the
        // otherwise-unstubbed /models endpoint.
        cy.intercept("GET", `**/projects/${projectId}/models*`, {
            statusCode: 200,
            body: {
                page: 1,
                pageSize: 5,
                totalPages: 1,
                totalRecords: 1,
                data: [
                    {
                        id: "6292d9ec-e821-4e4a-814e-3a315a4cb95e",
                        name: "Stroke Detection Model",
                        description: "Detects acute stroke from CT head scans.",
                        status: "PENDING",
                        ownerid: "c8b36b74-0cb6-4b3a-9608-68cd4d0d8162"
                    }
                ]
            }
        }).as("getModels");

        cy.visit(`/project/${projectId}`);
        cy.wait("@getProject");
        cy.wait("@getImageStatus");
        cy.demoPause();

        cy.getBySel("project-status-container").scrollIntoView();
        cy.demoPause();

        cy.getBySel(`trust-name-${uclhId}`).should("contain", "UCLH");
        cy.getBySel(`successful-imports-${uclhId}`).should("be.visible");
        cy.demoPause(1200);
    });
});
