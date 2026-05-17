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

// Records docs/source/assets/flip/stop-training.gif.
// Model is mid-training (getModelPostTrainingInitiatedStatus). Open the
// Actions menu, hit "Stop Training", confirm → POST /fl/stop/{modelId}.

const PROJECT_ID = "6fcbdd40-3675-45c9-899e-1a005e5245ba";
const MODEL_ID = "6292d9ec-e821-4e4a-814e-3a315a4cb95e";

describe("docs: stop training", () => {
    it("stops an in-progress training run", () => {
        cy.login();
        cy.intercept("GET", "/projects/*", { fixture: "project/getApprovedProject" });
        // canStopTraining requires status >= PREPARED (< RESULTS_UPLOADED).
        cy.intercept("POST", `/step/model/${MODEL_ID}`, {
            fixture: "model/getModelPostTrainingPreparedStatus"
        }).as("getModel");
        cy.intercept("GET", `/model/${MODEL_ID}/logs`, []);
        cy.intercept("POST", `/fl/stop/${MODEL_ID}`, { statusCode: 200, body: {} }).as("stopTraining");

        cy.visit(`/project/${PROJECT_ID}/model/${MODEL_ID}`);
        cy.wait("@getModel");
        cy.demoPause();

        cy.getBySel("user-btn").demoClick();
        cy.demoPause();

        cy.getBySel("stop-training-btn").demoClick();
        cy.demoPause();

        cy.getBySel("confirm-modal-btn").demoClick();
        cy.wait("@stopTraining");
        cy.demoPause(1500);
    });
});
