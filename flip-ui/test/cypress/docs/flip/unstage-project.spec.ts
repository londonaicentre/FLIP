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
// On a staged (but not approved) project: click "Unstage Project", confirm.

const PROJECT_ID = "6fcbdd40-3675-45c9-899e-1a005e5245ba";

describe("docs: unstage project", () => {
    it("unstages a previously staged project", () => {
        cy.login();
        cy.intercept("GET", `/projects/${PROJECT_ID}`, { fixture: "project/getStagedProject" });
        cy.intercept("GET", `/projects/${PROJECT_ID}/models*`, { body: { models: [], pagination: { totalCount: 0 } } });
        cy.intercept("POST", `/projects/${PROJECT_ID}/unstage`, { statusCode: 200, body: {} }).as("unstageProject");

        cy.visit(`/project/${PROJECT_ID}`);
        cy.demoPause();

        cy.getBySel("unstage-project-btn").demoClick();
        cy.demoPause();

        cy.getBySel("confirm-modal-btn").demoClick();
        cy.wait("@unstageProject");
        cy.demoPause(1500);
    });
});
