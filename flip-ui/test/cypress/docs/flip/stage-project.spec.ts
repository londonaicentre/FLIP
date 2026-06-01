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

// Records docs/source/assets/flip/stage-project.gif.
// Mirrors the happy path in test/cypress/integration/group-1/project/view_project.spec.ts
// ("With cohort query" -> "can stage with only one trust associated with a project").

describe("docs: stage project", () => {
    it("stages a project for approval", () => {
        const projectId = "6fcbdd40-3675-45c9-899e-1a005e5245ba";

        cy.login();
        cy.intercept("GET", "/users/*/permissions", { fixture: "user/getPermissionsResearcher" });
        cy.intercept("GET", "/projects/" + projectId, {
            fixture: "project/getProjectWithQueryNotApprovedUnstaged"
        }).as("getProject");
        cy.intercept("POST", `/projects/${projectId}/stage`, { statusCode: 200 }).as("stage");

        cy.visit("/project/" + projectId);
        cy.wait("@getProject");
        cy.demoPause();

        cy.getBySel("KCH-selector").demoClick();
        cy.demoPause();

        cy.getBySel("stage-project-btn").demoClick();
        cy.wait("@stage");
        cy.demoPause(1200);
    });
});
