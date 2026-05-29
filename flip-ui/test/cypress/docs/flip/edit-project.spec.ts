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

// Records docs/source/assets/flip/edit-project.gif.
// Mirrors the happy path in test/cypress/integration/group-1/project/view_project.spec.ts
// ("Editing Project" -> "Handles project editing", the name/description update step).

describe("docs: edit project", () => {
    it("edits an existing project's details", () => {
        cy.login();
        cy.intercept("GET", "/projects/*", { fixture: "project/getProject" }).as("getProject");
        cy.intercept("PUT", "/projects/*", { statusCode: 200, body: {} }).as("updateProject");

        cy.visit("/project/6fcbdd40-3675-45c9-899e-1a005e5245ba");
        cy.wait("@getProject");
        cy.demoPause();

        cy.getBySel("edit-project-btn").demoClick();
        cy.demoPause();

        cy.getBySel("project-name").clear().demoType("Stroke Detection Study");
        cy.demoPause();

        cy.getBySel("project-description").clear().demoType("Updated federated stroke detection model.");
        cy.demoPause();

        cy.getBySel("update-project-btn").demoClick();
        cy.wait("@updateProject");
        cy.contains("This project has been updated").should("be.visible");
        cy.demoPause(1200);
    });
});
