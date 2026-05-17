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

// Records docs/source/assets/flip/manage-files.gif.
// Shows the "Model Files" panel of a model that has multiple files already
// uploaded (getModelTrainAdditionalFiles fixture) — scroll the panel into
// view, dwell so the row icons read, then hover the first row.

const PROJECT_ID = "6fcbdd40-3675-45c9-899e-1a005e5245ba";
const MODEL_ID = "6292d9ec-e821-4e4a-814e-3a315a4cb95e";

describe("docs: manage uploaded files", () => {
    it("shows the uploaded-files panel for a model", () => {
        cy.login();
        cy.intercept("GET", "/projects/*", { fixture: "project/getApprovedProject" });
        cy.intercept("POST", `/step/model/${MODEL_ID}`, {
            fixture: "model/getModelTrainAdditionalFiles"
        }).as("getModel");
        cy.intercept("GET", `/model/${MODEL_ID}/logs`, []);

        cy.visit(`/project/${PROJECT_ID}/model/${MODEL_ID}`);
        cy.wait("@getModel");
        cy.demoPause();

        cy.contains("Model Files").scrollIntoView();
        cy.demoPause(2400);
    });
});
