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

// Records docs/source/assets/flip/edit-model.gif.
// Mirrors the happy path in test/cypress/integration/group-2/model/edit_model.spec.ts
// ("Allows user to edit the model").

describe("docs: edit model", () => {
    it("edits an existing model", () => {
        const projectId = "6fcbdd40-3675-45c9-899e-1a005e5245ba";
        const modelId = "6292d9ec-e821-4e4a-814e-3a315a4cb95e";

        cy.login();
        cy.intercept("GET", "/projects/*", { fixture: "project/getApprovedProject" });
        cy.intercept("POST", `/step/model/${modelId}`, { fixture: "model/getModel" }).as("getModel");
        cy.intercept("GET", `/model/${modelId}/logs`, []);
        cy.intercept("PUT", "/model/*", {
            statusCode: 200,
            body: {}
        }).as("editModel");

        cy.visit(`project/${projectId}/model/${modelId}`);
        cy.wait("@getModel");
        cy.demoPause();

        cy.getBySel("edit-model-btn").demoClick();
        cy.demoPause();

        cy.getBySel("model-name").clear().demoType("Updated Stroke Model");
        cy.demoPause();

        cy.getBySel("model-description").clear().demoType("Updated model description.");
        cy.demoPause();

        cy.getBySel("update-model-btn").demoClick();
        cy.wait("@editModel");
        cy.contains("This model has been updated.").should("be.visible");
        cy.demoPause(1200);
    });
});
