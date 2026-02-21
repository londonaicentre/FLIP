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




describe("Model Dashboard - Pre Training", () => {
    const projectId = "6fcbdd40-3675-45c9-899e-1a005e5245ba";
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
        cy.intercept("GET", `/model/${modelId}/logs`, [])
            .as("getLogs");
        // Intercept config.json file download for job type detection
        cy.intercept("GET", `/files/model/${modelId}/config.json`, {
            fixture: "model/configJsonStandard.json"
        }).as("getConfigJson");
    });

    it("redirects you back to the project if the model can not be found.", () => {
        cy.intercept("POST", `/step/model/${modelId}`, { statusCode: 404 })
            .as("getModel");

        cy.visit(`project/${projectId}/model/${modelId}`);
        cy.wait("@getModel");

        // ProjectId is from project/getProjectWithQuery
        cy.url().should("include", "/project/6fcbdd40-3675-45c9-899e-1a005e5245ba");
    });

    it("prevents user from initiating training if none of the required files are uploaded", () => {
        cy.intercept("POST", `/step/model/${modelId}`, { fixture: "model/getModel" })
            .as("getModel");
        cy.visit(`project/${projectId}/model/${modelId}`);
        cy.wait("@getModel");

        cy.contains("All required model files must be uploaded before starting training.").should("be.visible");
        cy.contains("Complete the following fields to initiate training.").should("be.visible");
        cy.getBySel("initiate-training-btn").should("be.disabled");

        cy.getBySel("data-enrichment-btn").click();
        cy.getBySel("initiate-training-btn").should("be.disabled");

        cy.getBySel("initiate-training-btn").should("be.disabled");

        cy.getBySel("trust-selection-0").click();
        cy.getBySel("trust-selection-1").click();
        cy.getBySel("initiate-training-btn").should("be.disabled");
    });

    it("does not allow the user to initiate training if any the required files are not uploaded", () => {
        cy.intercept("POST", `step/model/${modelId}`, { fixture: "model/getModelTrainMissingFile" })
            .as("getModel");
        cy.visit(`project/${projectId}/model/${modelId}`);
        cy.wait("@getModel");

        cy.getBySel("data-enrichment-btn").click();
        cy.getBySel("trust-selection-0").click();
        cy.getBySel("trust-selection-1").click();

        cy.getBySel("initiate-training-btn").should("be.disabled");

        // Should show which specific files are missing
        cy.contains("Missing required files:").should("be.visible");
    });

    it("does not allow the user to initiate training if the data enrichment button has not been checked", () => {
        cy.intercept("POST", `/step/model/${modelId}`, { fixture: "model/getModelTrain" })
            .as("getModel");

        cy.visit(`project/${projectId}/model/${modelId}`);
        cy.wait("@getModel");

        cy.getBySel("trust-selection-0").click();
        cy.getBySel("trust-selection-1").click();

        cy.getBySel("initiate-training-btn").click();

        cy.contains("Please confirm data enrichment.")
            .should("be.visible");
    });

    it("does not allow the user to initiate training if a minimum of one trust is not selected", () => {
        cy.intercept("POST", `/step/model/${modelId}`, { fixture: "model/getModelTrain" })
            .as("getModel");

        cy.visit(`project/${projectId}/model/${modelId}`);
        cy.wait("@getModel");

        cy.getBySel("data-enrichment-btn").click();

        cy.getBySel("initiate-training-btn").click();

        cy.contains("You must select a minimum of one trust for training.")
            .should("be.visible");
    });

    it("enables user to initiate training given all criteria is met", () => {
        cy.intercept("POST", `/step/model/${modelId}`, { fixture: "model/getModelTrain" })
            .as("getModel");

        cy.visit(`project/${projectId}/model/${modelId}`);
        cy.wait("@getModel");

        cy.getBySel("data-enrichment-btn").click();
        cy.getBySel("trust-selection-0").click();
        cy.getBySel("trust-selection-1").click();

        cy.intercept("POST", `/fl/initiate/${modelId}`, { statusCode: 200 })
            .as("initialiseTraining");
        cy.getBySel("initiate-training-btn").click();
        cy.wait("@initialiseTraining");

        cy.get("@initialiseTraining").its("request.body").should("deep.equal", {
            "trusts": [
                "KCH",
                "UCLH"
            ]
        });

        // No option to change what was selected for training
        cy.getBySel("data-enrichment-btn").should("not.exist");
        cy.getBySel("trust-selection-0").should("not.exist");
        cy.getBySel("trust-selection-1").should("not.exist");
    });

    it("enables user to initiate training with additional files given all criteria is met", () => {
        cy.intercept("POST", `/step/model/${modelId}`, { fixture: "model/getModelTrainAdditionalFiles" })
            .as("getModel");

        cy.visit(`project/${projectId}/model/${modelId}`);
        cy.wait("@getModel");

        cy.getBySel("data-enrichment-btn").click();
        cy.getBySel("trust-selection-0").click();
        cy.getBySel("trust-selection-1").click();

        cy.intercept("POST", `/fl/initiate/${modelId}`, { statusCode: 200 })
            .as("initialiseTraining");
        cy.getBySel("initiate-training-btn").click();
        cy.wait("@initialiseTraining");

        cy.get("@initialiseTraining").its("request.body").should("deep.equal", {
            "trusts": [
                "KCH",
                "UCLH"
            ]
        });

        // No option to change what was selected for training
        cy.getBySel("data-enrichment-btn").should("not.exist");
        cy.getBySel("trust-selection-0").should("not.exist");
        cy.getBySel("trust-selection-1").should("not.exist");
    });

    it("displays correct cohort query value and results", () => {


        cy.intercept("POST", `/step/model/${modelId}`, { fixture: "model/getModel" })
            .as("getModel");
        cy.visit(`project/${projectId}/model/${modelId}`);
        cy.wait("@getModel");

        cy.intercept("GET", "cohort/*",
            { fixture: "cohort/cohortQueryResultsOmop" })
            .as("cohortResults");

        cy.intercept("GET", "projects/" + projectId,
            { fixture: "project/getProjectWithQuery" })
            .as("project");

        cy.wait("@projectWithQuery");

        cy.getBySel("view-results-btn").click()
            .wait("@cohortResults")
            .then(() => {
                cy.get("canvas").eq(0).scrollIntoView().should("be.visible");
                cy.get("canvas").eq(1).scrollIntoView().should("be.visible");
                cy.get("canvas").eq(2).scrollIntoView().should("be.visible");
            });
    });
});

describe("Model Dashboard - Pre Training with only one approved trust", () => {
    const projectId = "13fe7e36-2310-432d-8c59-91da99b80988";
    const modelId = "6292d9ec-e821-4e4a-814e-3a315a4cb95e";

    before(() => {
        cy.login();
    });

    beforeEach(() => {
        cy.restoreLocalStorage();
        cy.intercept("GET",
            "/projects/" + projectId,
            { fixture: "project/getApprovedProjectOneTrust" }
        ).as("project");
        cy.intercept("GET", `/model/${modelId}/logs`, [])
            .as("getLogs");
    });

    it("does not allow the user to initiate training if the approved trust is not selected", () => {
        cy.intercept("POST", `/step/model/${modelId}`, { fixture: "model/getModelTrain" })
            .as("getModel");

        cy.visit(`project/${projectId}/model/${modelId}`);
        cy.wait("@getModel");

        cy.getBySel("data-enrichment-btn").click();

        cy.getBySel("initiate-training-btn").click();

        cy.contains("You must select a minimum of one trust for training.")
            .should("be.visible");
    });

    it("enables user to initiate training given all criteria is met", () => {
        cy.intercept("POST", `/step/model/${modelId}`, { fixture: "model/getModelTrain" })
            .as("getModel");

        cy.visit(`project/${projectId}/model/${modelId}`);
        cy.wait("@getModel");

        cy.getBySel("data-enrichment-btn").click();
        cy.getBySel("trust-selection-0").click();

        cy.intercept("POST", `/fl/initiate/${modelId}`, { statusCode: 200 })
            .as("initialiseTraining");
        cy.getBySel("initiate-training-btn").click();
        cy.wait("@initialiseTraining");

        cy.get("@initialiseTraining").its("request.body").should("deep.equal", {
            "trusts": [
                "KCH"
            ]
        });

        // No option to change what was selected for training
        cy.getBySel("data-enrichment-btn").should("not.exist");
        cy.getBySel("trust-selection-0").should("not.exist");
    });

    it("enables user to initiate training with additional files given all criteria is met", () => {
        cy.intercept("POST", `/step/model/${modelId}`, { fixture: "model/getModelTrainAdditionalFiles" })
            .as("getModel");

        cy.visit(`project/${projectId}/model/${modelId}`);
        cy.wait("@getModel");

        cy.getBySel("data-enrichment-btn").click();
        cy.getBySel("trust-selection-0").click();

        cy.intercept("POST", `/fl/initiate/${modelId}`, { statusCode: 200 })
            .as("initialiseTraining");
        cy.getBySel("initiate-training-btn").click();
        cy.wait("@initialiseTraining");

        cy.get("@initialiseTraining").its("request.body").should("deep.equal", {
            "trusts": [
                "KCH"
            ]
        });

        // No option to change what was selected for training
        cy.getBySel("data-enrichment-btn").should("not.exist");
        cy.getBySel("trust-selection-0").should("not.exist");
    });
});
