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

// Records docs/source/assets/flip/delete-model.gif.
// Mirrors the happy path in test/cypress/integration/group-2/model/delete_model.spec.ts
// ("Delete Model: With Permissions" -> "Allows the user to delete the model").

describe("docs: delete model", () => {
    it("deletes a model", () => {
        const projectId = "6fcbdd40-3675-45c9-899e-1a005e5245ba";
        const modelId = "6292d9ec-e821-4e4a-814e-3a315a4cb95e";
        const modelName = "Test Model";

        cy.login();
        cy.intercept("GET", "/projects/*", { fixture: "project/getApprovedProject" });
        cy.intercept("POST", `/step/model/${modelId}`, { fixture: "model/getModel" }).as("getModel");
        cy.intercept("GET", `/model/${modelId}/logs`, []);
        cy.intercept("GET", "/projects/*/models?pageSize=5", { fixture: "model/getLatestModels" });
        cy.intercept("DELETE", `/model/${modelId}`, {
            statusCode: 200,
            body: {}
        }).as("deleteModel");

        cy.visit(`project/${projectId}/model/${modelId}`);
        cy.wait("@getModel");
        cy.demoPause();

        cy.getBySel("edit-model-btn").demoClick();
        cy.demoPause();

        cy.getBySel("delete-model-btn").demoClick();
        cy.demoPause();

        cy.getBySel("confirmation-input").demoType(modelName);
        cy.demoPause();

        cy.getBySel("confirm-modal-btn").demoClick();
        cy.wait("@deleteModel");
        cy.contains("Model deleted").should("be.visible");
        cy.demoPause(1200);
    });
});
