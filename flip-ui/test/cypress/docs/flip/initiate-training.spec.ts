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

// Records docs/source/assets/flip/initiate-training.gif.
// Mirrors the happy path in test/cypress/integration/group-6/model_dashboard_pre_training.spec.ts
// ("enables user to initiate training given all criteria is met").

describe("docs: initiate training", () => {
    it("starts training a model", () => {
        const projectId = "6fcbdd40-3675-45c9-899e-1a005e5245ba";
        const modelId = "6292d9ec-e821-4e4a-814e-3a315a4cb95e";

        cy.login();
        cy.intercept("GET", "/projects/" + projectId, { fixture: "project/getProjectWithQuery" });
        cy.intercept("POST", `/step/model/${modelId}`, { fixture: "model/getModelTrain" }).as("getModel");
        cy.intercept("GET", `/model/${modelId}/logs`, []).as("getLogs");
        cy.intercept("GET", `/model/${modelId}/metrics`, []).as("getMetrics");
        cy.intercept("GET", `/files/model/${modelId}/config.json`, {
            fixture: "model/configJsonStandard.json"
        }).as("getConfigJson");
        cy.intercept("GET", "/model/job-types", {
            standard: ["trainer.py", "validator.py", "models.py", "config.json"]
        }).as("getJobTypes");

        cy.visit(`project/${projectId}/model/${modelId}`);
        cy.wait("@getModel");
        cy.demoPause();

        cy.getBySel("data-enrichment-btn").demoClick();
        cy.demoPause();

        cy.getBySel("trust-selection-0").demoClick();
        cy.getBySel("trust-selection-1").demoClick();
        cy.demoPause();

        cy.intercept("POST", `/fl/initiate/${modelId}`, { statusCode: 200 }).as("initiateTraining");
        cy.getBySel("initiate-training-btn").demoClick();
        cy.wait("@initiateTraining");

        // Once training is initiated the data-enrichment / trust-selection
        // controls are replaced by the in-progress training view.
        cy.getBySel("data-enrichment-btn").should("not.exist");
        cy.demoPause(1200);
    });
});
