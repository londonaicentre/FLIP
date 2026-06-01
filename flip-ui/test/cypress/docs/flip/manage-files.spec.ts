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
// Mirrors the happy path in test/cypress/integration/group-2/model/upload_model.spec.ts
// ("displays uploaded status when model file is uploaded successfully").

describe("docs: manage files", () => {
    it("uploads a file to a model", () => {
        const projectId = "6fcbdd40-3675-45c9-899e-1a005e5245ba";
        const modelId = "6292d9ec-e821-4e4a-814e-3a315a4cb95e";
        const uploadOrigin = "https://flip-uploaded-model-files-bucket-test.s3.eu-west-2.amazonaws.com";
        const presignedPolicy = {
            url: `${uploadOrigin}/`,
            fields: {
                "key": `${modelId}/trainer.py`,
                "Content-Type": "text/x-python",
                "policy": "stub-policy",
                "x-amz-signature": "stub-signature"
            },
            maxBytes: 100 * 1024 * 1024
        };

        cy.login();
        cy.intercept("GET", "/projects/" + projectId, { fixture: "project/getProjectWithQuery" });
        cy.intercept("POST", `/step/model/${modelId}`, { fixture: "model/getModel" }).as("getModel");
        cy.intercept("GET", `/model/${modelId}/logs`, []).as("getLogs");
        cy.intercept("POST", `/files/preSignedUrl/model/${modelId}`, presignedPolicy).as("fileLambda");
        cy.intercept("POST", `${uploadOrigin}/`, { statusCode: 204 }).as("fileUpload");

        cy.visit(`project/${projectId}/model/${modelId}`);
        cy.wait("@getModel");
        cy.demoPause();

        cy.getBySel("upload-file-btn").scrollIntoView();
        cy.getBySel("upload-file-btn").selectFile("test/cypress/fixtures/files/trainer.py", { action: "drag-drop" });
        cy.demoPause();

        cy.getBySel("file-upload-status-scanning").should("be.visible");
        cy.demoPause();

        cy.intercept("POST", `/step/model/${modelId}`, { fixture: "model/getModelFileCompleted" })
            .as("getModelFile");
        cy.wait("@getModelFile", { requestTimeout: 20000 });

        cy.getBySel("file-upload-status-completed").should("be.visible");
        cy.demoPause(1200);
    });
});
