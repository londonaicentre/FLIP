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




import { validProjectWithQuery } from "../common";

describe("Model Dashboard - Post Training", () => {
    const projectId = validProjectWithQuery.id;
    const modelId = "6292d9ec-e821-4e4a-814e-3a315a4cb95e";

    before(() => {
        cy.login();
    });

    beforeEach(() => {
        cy.restoreLocalStorage();

        cy.intercept("GET",
            "/projects/" + projectId,
            { fixture: "project/getProjectWithQuery" }
        ).as("projectWithQuery");

        cy.intercept("GET", `/model/${modelId}/logs`, { fixture: "model/logsPostTraining" })
            .as("getLogs");
    });

    it("does not allow the user to stop training if the model status is anything before 'PREPARED'", () => {
        cy.intercept("POST", `/step/model/${modelId}`, { fixture: "model/getModelPostTrainingInitiatedStatus" })
            .as("getModel");

        cy.visit(`project/${projectId}/model/${modelId}`);
        cy.wait("@getModel");

        cy.getBySel("initiate-training-btn").should("be.disabled");
        cy.getBySel("user-btn").click();
        cy.getBySel("stop-training-btn").should("be.disabled");
    });


    it("allows the user to stop training once it has the status of 'PREPARED'", () => {
        cy.intercept("POST", `/step/model/${modelId}`, { fixture: "model/getModelPostTrainingPreparedStatus" })
            .as("getModel");

        cy.intercept("POST", `/fl/stop/${modelId}`, { statusCode: 200 })
            .as("stopTraining");

        cy.visit(`project/${projectId}/model/${modelId}`);
        cy.wait("@getModel");

        cy.getBySel("initiate-training-btn").should("be.disabled");
        cy.getBySel("user-btn").click();
        cy.getBySel("stop-training-btn").should("be.visible").click();
        cy.getBySel("confirm-modal-btn").click();

        cy.wait("@stopTraining");
    });
});
