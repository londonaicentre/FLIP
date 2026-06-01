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

// Records docs/source/assets/flip/imaging-status-filter.gif.
// Mirrors the happy path in test/cypress/integration/group-5/view_project.spec.ts
// ("shows the imaging status"). Reuses the globalIntercepts mock for
// /projects/*/image/status (project/imagingProjectStatus: UCLH + KCH).

describe("docs: imaging status filter", () => {
    it("filters the imaging project status by trust", () => {
        cy.login();

        const projectId = "6fcbdd40-3675-45c9-899e-1a005e5245ba";

        cy.intercept("GET", `/projects/${projectId}`, { fixture: "project/getApprovedProject" }).as("getProject");

        cy.visit(`/project/${projectId}`);
        cy.wait("@getProject");
        cy.demoPause();

        cy.getBySel("project-status-container").scrollIntoView();
        cy.demoPause();

        cy.getBySel("filter-project-status").demoType("UCLH", { force: true });
        cy.demoPause();

        cy.getBySel("trust-name-a9b4cbe9-5754-4b7c-b2ae-6de495610f1b").contains("UCLH").should("be.visible");
        cy.demoPause(1200);
    });
});
