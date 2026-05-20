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

// Records docs/source/assets/flip/create-model.gif.
// Mirrors the happy path in integration/group-2/model/create_model.spec.ts.

const PROJECT_ID = "6fcbdd40-3675-45c9-899e-1a005e5245ba";

describe("docs: create model", () => {
    it("creates a model under an existing project", () => {
        cy.login();
        cy.intercept("GET", `/projects/${PROJECT_ID}`, { fixture: "project/getProjectWithQuery" });
        cy.intercept("GET", `/projects/${PROJECT_ID}/models*`, { fixture: "model/getModels" }).as("getModels");
        cy.intercept("POST", "/model", { statusCode: 200, body: { id: PROJECT_ID } }).as("createModel");
        cy.intercept("POST", `/step/model/${PROJECT_ID}`, { fixture: "model/getModel" });

        cy.visit(`/project/${PROJECT_ID}`);
        cy.wait("@getModels");
        cy.demoPause();

        cy.getBySel("add-model-btn").first().demoClick();
        cy.demoPause();

        cy.getBySel("model-name").demoType("Stroke Segmentation v2");
        cy.demoPause(300);

        cy.getBySel("model-description").demoType("nnU-Net baseline on the curated stroke cohort.");
        cy.demoPause(300);

        cy.getBySel("create-model-btn").first().demoClick();
        cy.wait("@createModel");
        cy.demoPause(1500);
    });
});
