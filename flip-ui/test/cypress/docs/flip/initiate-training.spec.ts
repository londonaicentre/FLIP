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
//
// `canTrain` on the model page gates Initiate Training behind:
//   - status PENDING (and not stopped/errored)
//   - allFilesUploaded (filenames match requiredFiles for the resolved job type)
//   - allFilesPassScan (every file at status COMPLETED)
//   - modelData.query truthy
//
// The required-files list comes from GET /model/job-types crossed with
// the job_type field of GET /files/model/{id}/config.json. Both are
// intercepted below so the watcher inside pages/project/[projectId]/model/
// [modelId]/index.vue can flip allFilesUploaded/PassScan true.

const PROJECT_ID = "6fcbdd40-3675-45c9-899e-1a005e5245ba";
const MODEL_ID = "6292d9ec-e821-4e4a-814e-3a315a4cb95e";
const STANDARD_REQUIRED_FILES = ["trainer.py", "validator.py", "models.py", "config.json"];

describe("docs: initiate training", () => {
    it("starts FL training on the first participating trust", () => {
        cy.login();
        cy.intercept("GET", "/projects/*", { fixture: "project/getApprovedProject" });
        cy.intercept("POST", `/step/model/${MODEL_ID}`, { fixture: "model/getModelTrain" }).as("getModel");
        cy.intercept("GET", `/model/${MODEL_ID}/logs`, []);
        cy.intercept("GET", "/model/job-types", { body: { standard: STANDARD_REQUIRED_FILES } });
        cy.intercept(
            "GET",
            `/files/model/${MODEL_ID}/config.json`,
            { fixture: "model/configJsonStandard" }
        );
        cy.intercept("POST", `/fl/initiate/${MODEL_ID}`, { statusCode: 200, body: {} }).as("initTraining");

        cy.visit(`/project/${PROJECT_ID}/model/${MODEL_ID}`);
        cy.wait("@getModel");
        cy.demoPause();

        cy.getBySel("data-enrichment-btn").demoClick();
        cy.demoPause(300);

        cy.getBySel("trust-selection-0").demoClick();
        cy.demoPause();

        cy.getBySel("initiate-training-btn").demoClick();
        cy.wait("@initTraining");
        cy.demoPause(1500);
    });
});
