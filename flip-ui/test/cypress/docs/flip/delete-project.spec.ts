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

// Records docs/source/assets/flip/delete-project.gif.
// Open the project → edit drawer → delete (with name confirmation modal).

const PROJECT_ID = "6fcbdd40-3675-45c9-899e-1a005e5245ba";
const PROJECT_NAME = "Stroke Test Project";

describe("docs: delete project", () => {
    it("deletes a project after confirming its name", () => {
        cy.login();
        cy.intercept("GET", `/projects/${PROJECT_ID}`, { fixture: "project/getProject" });
        cy.intercept("GET", `/projects/${PROJECT_ID}/models*`, { body: { models: [], pagination: { totalCount: 0 } } });
        cy.intercept("DELETE", `/projects/${PROJECT_ID}`, { statusCode: 200, body: {} }).as("deleteProject");

        cy.visit(`/project/${PROJECT_ID}`);
        cy.demoPause();

        cy.getBySel("edit-project-btn").demoClick();
        cy.demoPause();

        cy.getBySel("delete-project-btn").demoClick();
        cy.demoPause();

        cy.getBySel("confirmation-input").demoType(PROJECT_NAME);
        cy.demoPause(300);

        cy.getBySel("confirm-modal-btn").demoClick();
        cy.wait("@deleteProject");
        cy.demoPause(1500);
    });
});
