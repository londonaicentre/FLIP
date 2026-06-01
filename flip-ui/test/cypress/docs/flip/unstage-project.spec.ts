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

// Records docs/source/assets/flip/unstage-project.gif.
// Mirrors the happy path in test/cypress/integration/group-5/view_project.spec.ts
// ("Project Page: STAGED" -> "can unstage a staged project").

describe("docs: unstage project", () => {
    it("unstages a staged project", () => {
        const projectId = "6fcbdd40-3675-45c9-899e-1a005e5245ba";

        cy.login();
        cy.intercept("GET", "/projects/*/image/status", { fixture: "project/getProjectStatus" });
        cy.intercept("GET", "/projects/" + projectId, { fixture: "project/getStagedProject" }).as("getProject");
        cy.intercept("POST", "/projects/*/unstage", { statusCode: 200 }).as("unstageProject");

        cy.visit("/project/" + projectId);
        cy.wait("@getProject");
        cy.demoPause();

        cy.getBySel("unstage-project-btn").demoClick();
        cy.demoPause();

        cy.getBySel("confirm-modal-btn").demoClick();
        cy.wait("@unstageProject");

        cy.contains("The project has been unstaged.").should("be.visible");
        cy.demoPause(1200);
    });
});
