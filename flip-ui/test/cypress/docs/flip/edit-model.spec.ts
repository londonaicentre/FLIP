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
// Cribs the flow from the (currently skipped) integration/group-2/model/
// edit_model.spec.ts — open a model that hasn't started training, hit
// "Edit Model", update the name/description, save.

const PROJECT_ID = "6fcbdd40-3675-45c9-899e-1a005e5245ba";
const MODEL_ID = "6292d9ec-e821-4e4a-814e-3a315a4cb95e";

describe("docs: edit model", () => {
    it("renames a pending model", () => {
        cy.login();
        cy.intercept("GET", "/projects/*", { fixture: "project/getApprovedProject" });
        cy.intercept("POST", `/step/model/${MODEL_ID}`, { fixture: "model/getModel" }).as("getModel");
        cy.intercept("GET", `/model/${MODEL_ID}/logs`, []);
        cy.intercept("PUT", "/model/*", { statusCode: 200, body: {} }).as("editModel");

        cy.visit(`/project/${PROJECT_ID}/model/${MODEL_ID}`);
        cy.wait("@getModel");
        cy.demoPause();

        cy.getBySel("edit-model-btn").demoClick();
        cy.demoPause();

        cy.getBySel("model-name").clear().demoType("Stroke Segmentation v2.1");
        cy.demoPause(300);

        cy.getBySel("model-description").clear().demoType("Refined hyperparameters after the v2 sweep.");
        cy.demoPause(300);

        cy.getBySel("update-model-btn").demoClick();
        cy.wait("@editModel");
        cy.demoPause(1500);
    });
});
