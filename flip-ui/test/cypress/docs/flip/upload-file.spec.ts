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

// Records docs/source/assets/flip/upload-file.gif.
// Cribs the happy-path "uploads a model file" flow from
// integration/group-2/model/upload_model.spec.ts.

const PROJECT_ID = "6fcbdd40-3675-45c9-899e-1a005e5245ba";
const MODEL_ID = "6292d9ec-e821-4e4a-814e-3a315a4cb95e";
const SIGNED_URL = "https://flip-uploaded-model-files-bucket-test.s3.eu-west-2.amazonaws.com/test";

describe("docs: upload model file", () => {
    it("uploads trainer.py to the model", () => {
        cy.login();
        cy.intercept("GET", `/projects/${PROJECT_ID}`, { fixture: "project/getProjectWithQuery" });
        cy.intercept("POST", `/step/model/${MODEL_ID}`, { fixture: "model/getModel" }).as("getModel");
        cy.intercept("GET", `/model/${MODEL_ID}/logs`, []);
        cy.intercept("POST", `/files/preSignedUrl/model/${MODEL_ID}`, SIGNED_URL).as("preSignedUrl");
        cy.intercept("PUT", SIGNED_URL, { statusCode: 200 }).as("s3Upload");

        cy.visit(`/project/${PROJECT_ID}/model/${MODEL_ID}`);
        cy.wait("@getModel");
        cy.demoPause();

        cy.getBySel("upload-file-btn").scrollIntoView();
        cy.demoPause(400);
        cy.getBySel("upload-file-btn").selectFile("test/cypress/fixtures/files/trainer.py", {
            action: "drag-drop"
        });
        cy.wait("@preSignedUrl");
        cy.wait("@s3Upload");
        cy.demoPause(1800);
    });
});
