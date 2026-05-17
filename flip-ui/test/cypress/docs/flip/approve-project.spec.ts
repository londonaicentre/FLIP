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

// Records docs/source/assets/flip/approve-project.gif.
// As an admin (CanApproveProjects in the default getPermissions fixture),
// flip the per-trust approval switches on a staged project, then save.

const PROJECT_ID = "6fcbdd40-3675-45c9-899e-1a005e5245ba";

describe("docs: approve project", () => {
    it("approves a staged project across both trusts", () => {
        cy.login();
        cy.intercept("GET", `/projects/${PROJECT_ID}`, { fixture: "project/getStagedProject" });
        cy.intercept("GET", `/projects/${PROJECT_ID}/models*`, { body: { models: [], pagination: { totalCount: 0 } } });
        cy.intercept("POST", `/step/project/${PROJECT_ID}/approve`, { statusCode: 200, body: {} }).as("approveProject");

        cy.visit(`/project/${PROJECT_ID}`);
        cy.demoPause();

        cy.getBySel("trust-staged-0").demoClick();
        cy.demoPause(300);

        cy.getBySel("trust-staged-1").demoClick();
        cy.demoPause();

        cy.getBySel("approve-project-btn").demoClick();
        cy.wait("@approveProject");
        cy.demoPause(1500);
    });
});
