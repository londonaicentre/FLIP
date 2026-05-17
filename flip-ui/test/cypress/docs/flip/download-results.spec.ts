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
// On a model whose training finished (getModelPostTraining fixture), open the
// Actions menu, hit "Download Results" → GET /files/model/{id}/fl/results.
// The Download Results button has no data-test attribute, so we click by text.

const PROJECT_ID = "6fcbdd40-3675-45c9-899e-1a005e5245ba";
const MODEL_ID = "6292d9ec-e821-4e4a-814e-3a315a4cb95e";

describe("docs: download training results", () => {
    it("downloads the results of a finished training run", () => {
        cy.login();
        cy.intercept("GET", "/projects/*", { fixture: "project/getApprovedProject" });
        // canDownloadResults gates on status ∈ {RESULTS_UPLOADED, STOPPED, ERROR}.
        // No bundled fixture lands at RESULTS_UPLOADED, so patch the post-training
        // fixture in place before serving it.
        cy.fixture("model/getModelPostTraining").then((model) => {
            model.status = "RESULTS_UPLOADED";
            cy.intercept("POST", `/step/model/${MODEL_ID}`, { body: model }).as("getModel");
        });
        cy.intercept("GET", `/model/${MODEL_ID}/logs`, []);
        cy.intercept("GET", `/files/model/${MODEL_ID}/fl/results`, {
            statusCode: 200,
            body: ["https://example.com/results.zip"]
        }).as("getResultsUrls");

        cy.visit(`/project/${PROJECT_ID}/model/${MODEL_ID}`);
        cy.wait("@getModel");
        cy.demoPause();

        cy.getBySel("user-btn").demoClick();
        cy.demoPause();

        cy.contains("button", "Download Results").demoClick();
        cy.wait("@getResultsUrls");
        cy.demoPause(1500);
    });
});
