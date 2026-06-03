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

// Records docs/source/assets/flip/fl-status.gif.
// Mirrors the happy path in test/cypress/integration/group-6/model_dashboard_post_training.spec.ts
// (in-progress "PREPARED" model dashboard) — shows the per-trust FL training timeline.

describe("docs: fl status", () => {
    it("views the federated-learning training status for a model", () => {
        const projectId = "6fcbdd40-3675-45c9-899e-1a005e5245ba";
        const modelId = "6292d9ec-e821-4e4a-814e-3a315a4cb95e";

        cy.login();
        cy.intercept("GET", "/projects/" + projectId, { fixture: "project/getProjectWithQuery" });
        cy.intercept("POST", `/step/model/${modelId}`, { fixture: "model/getModelPostTrainingPreparedStatus" })
            .as("getModel");
        cy.intercept("GET", `/model/${modelId}/logs`, { fixture: "model/logsPostTraining" }).as("getLogs");
        cy.intercept("GET", `/model/${modelId}/metrics`, []).as("getMetrics");

        cy.visit(`project/${projectId}/model/${modelId}`);
        cy.wait("@getModel");
        cy.wait("@getLogs");
        cy.demoPause();

        cy.contains("sending the training request...").scrollIntoView().should("be.visible");
        cy.demoPause(1200);
    });
});
