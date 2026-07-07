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

// Records docs/source/assets/flip/download-results.gif.
// Mirrors the happy path in test/cypress/integration/group-6/model_dashboard_post_training.spec.ts.

describe("docs: download results", () => {
    it("downloads the training results for a completed model", () => {
        cy.login();

        const projectId = "6fcbdd40-3675-45c9-899e-1a005e5245ba";
        const modelId = "6292d9ec-e821-4e4a-814e-3a315a4cb95e";

        cy.intercept("GET", `/projects/${projectId}`, { fixture: "project/getProjectWithQuery" }).as("getProject");
        cy.intercept("POST", `/step/model/${modelId}`, {
            body: {
                modelId,
                modelName: "CS Test",
                modelDescription: "A Model to test with",
                projectId,
                status: "RESULTS_UPLOADED",
                query: {
                    id: "54af9df8-b28b-4db9-8591-11ddda59f9be",
                    name: "A query",
                    query: "*:*",
                    results: []
                },
                files: [
                    {
                        id: "8c81bfe0-b6be-4067-9657-116d30c6d519",
                        name: "trainer.py",
                        status: "COMPLETED",
                        size: 3751,
                        type: "text/x-python",
                        tag: null
                    }
                ]
            }
        }).as("getModel");
        cy.intercept("GET", `/model/${modelId}/logs`, { fixture: "model/logsPostTraining" });
        cy.intercept("GET", `/model/${modelId}/metrics`, { body: [] });
        cy.intercept("GET", `/files/model/${modelId}/fl/results`, {
            body: ["https://example.com/results/model-results.zip"]
        }).as("getResults");

        cy.visit(`/project/${projectId}/model/${modelId}`);
        cy.wait("@getModel");
        cy.demoPause();

        cy.getBySel("download-results-btn").demoClick();
        cy.wait("@getResults");
        cy.demoPause(1200);
    });
});
