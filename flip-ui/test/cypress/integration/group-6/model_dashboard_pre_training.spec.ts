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
        // Intercept config.json file download for job type detection. The
        // endpoint returns a presigned URL {url, fileName} rather than the
        // file bytes directly (FLIP#784), so the byte fetch that follows
        // needs its own intercept too.
        cy.intercept("GET", `/files/model/${modelId}/config.json`, {
            body: { url: "https://fake-presigned.example.com/config.json", fileName: "config.json" }
        }).as("getConfigJson");
        cy.intercept("GET", "https://fake-presigned.example.com/config.json", {
            fixture: "model/configJsonStandard.json"
        }).as("getConfigJsonBytes");
        // The page calls /model/job-types in onBeforeMount and again from
        // file-service.getJobTypeFromConfig; without a stub the request
        // hangs/404s, jobTypes stays empty, and the watcher that gates
        // `requiredFiles` / `allFilesUploaded` / `readyToTrain` never
        // unlocks the initiate-training-btn.
        cy.intercept("GET", "/model/job-types", {
            standard: ["trainer.py", "validator.py", "models.py", "config.json"]
        }).as("getJobTypes");
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

        // Training.vue shows the dynamic missingFilesMessage when required
        // files are defined but not uploaded ("For job type X, required
        // files are: ... <br/>Missing: ..."), and falls back to this
        // generic message only when requiredFiles is empty. The fixture
        // (getModel) declares required files, so we assert on the dynamic
        // message instead.
        cy.contains("required files are:").should("be.visible");
        cy.contains("Missing:").should("be.visible");
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

        // The Training.vue alert lists specific missing files in the
        // form "For job type X, required files are: ... <br/>Missing: a.py".
        cy.contains("Missing:").should("be.visible");
    });

    it("does not allow the user to initiate training if the data enrichment button has not been checked", () => {
        cy.intercept("POST", `/step/model/${modelId}`, { fixture: "model/getModelTrain" })
            .as("getModel");

        cy.visit(`project/${projectId}/model/${modelId}`);
        cy.wait("@getModel");

        cy.getBySel("trust-selection-0").click();
        cy.getBySel("trust-selection-1").click();

        // The page disables Initiate Training until the run options are
        // complete, rather than letting the click bounce off the schema.
        cy.getBySel("initiate-training-btn").should("be.disabled");

        // Confirming enrichment is the only missing criterion, so it unlocks the button.
        cy.getBySel("data-enrichment-btn").click();
        cy.getBySel("initiate-training-btn").should("be.enabled");
    });

    it("does not allow the user to initiate training if a minimum of one trust is not selected", () => {
        cy.intercept("POST", `/step/model/${modelId}`, { fixture: "model/getModelTrain" })
            .as("getModel");

        cy.visit(`project/${projectId}/model/${modelId}`);
        cy.wait("@getModel");

        cy.getBySel("data-enrichment-btn").click();

        cy.getBySel("initiate-training-btn").should("be.disabled");

        // Selecting a trust is the only missing criterion, so it unlocks the button.
        cy.getBySel("trust-selection-0").click();
        cy.getBySel("initiate-training-btn").should("be.enabled");
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
            "trust_ids": [
                "SOMEIDFORKCH",
                "SOMEIDFORUCLH"
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
            "trust_ids": [
                "SOMEIDFORKCH",
                "SOMEIDFORUCLH"
            ]
        });

        // No option to change what was selected for training
        cy.getBySel("data-enrichment-btn").should("not.exist");
        cy.getBySel("trust-selection-0").should("not.exist");
        cy.getBySel("trust-selection-1").should("not.exist");
    });

    // The "displays correct cohort query value and results" test that lived
    // here drove a view-results-btn click on the model dashboard. The page
    // redesign moved the cohort-query card (with view-results-btn) onto the
    // project route — QueryDetails.vue is no longer mounted on /model/<id>.
    // Cohort-query UI behavior is covered by the cohort specs (group-6 cohort/
    // and the QueryResultCharts vitest suite), so the test was dropped rather
    // than ported to the project page.
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
        cy.intercept("GET", `/files/model/${modelId}/config.json`, {
            body: { url: "https://fake-presigned.example.com/config.json", fileName: "config.json" }
        }).as("getConfigJson");
        cy.intercept("GET", "https://fake-presigned.example.com/config.json", {
            fixture: "model/configJsonStandard.json"
        }).as("getConfigJsonBytes");
        cy.intercept("GET", "/model/job-types", {
            standard: ["trainer.py", "validator.py", "models.py", "config.json"]
        }).as("getJobTypes");
    });

    it("does not allow the user to initiate training if the approved trust is not selected", () => {
        cy.intercept("POST", `/step/model/${modelId}`, { fixture: "model/getModelTrain" })
            .as("getModel");

        cy.visit(`project/${projectId}/model/${modelId}`);
        cy.wait("@getModel");

        cy.getBySel("data-enrichment-btn").click();

        cy.getBySel("initiate-training-btn").should("be.disabled");

        // Selecting the approved trust is the only missing criterion, so it unlocks the button.
        cy.getBySel("trust-selection-0").click();
        cy.getBySel("initiate-training-btn").should("be.enabled");
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
            "trust_ids": [
                "SOMEIDFORKCH"
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
            "trust_ids": [
                "SOMEIDFORKCH"
            ]
        });

        // No option to change what was selected for training
        cy.getBySel("data-enrichment-btn").should("not.exist");
        cy.getBySel("trust-selection-0").should("not.exist");
    });
});
