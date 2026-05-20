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

// Records docs/source/assets/flip/create-project.gif.
// Mirrors the happy path from integration/group-3/project/create_project.spec.ts
// ("successfully creates a new project when name and description is provided")
// with demo pacing.

const PROJECT_ID = "6fcbdd40-3675-45c9-899e-1a005e5245ba";

describe("docs: create project", () => {
    it("creates a new project", () => {
        cy.login();
        cy.intercept("GET", "/projects?pageNumber=1&pageSize=20", { fixture: "project/getProjectsEmpty" });
        cy.intercept("GET", `/projects/${PROJECT_ID}`, { fixture: "project/getProjectWithQuery" });
        cy.intercept("POST", "/projects", { statusCode: 200, body: { id: PROJECT_ID } }).as("createProject");

        cy.visit("/projects");
        cy.demoPause();

        cy.getBySel("add-project-btn").demoClick();
        cy.demoPause();

        cy.getBySel("project-name").demoType("Stroke Imaging Cohort");
        cy.demoPause();

        cy.getBySel("project-description").demoType("Retrospective MR/CT collection across partner trusts.");
        cy.demoPause();

        cy.getBySel("create-project-btn").demoClick();
        cy.wait("@createProject");
        cy.demoPause(1200);
    });
});
