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




import { validProject } from "../../common";

describe("Upload Model Files", () => {
    const projectId = validProject.id;
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

    before(() => {
        cy.login();
    });

    beforeEach(() => {
        cy.restoreLocalStorage();

        // simulate default model dashboard state
        cy.intercept("GET",
            "/projects/" + validProject.id,
            { fixture: "project/getProjectWithQuery" }
        ).as("projectWithQuery");
        cy.intercept("POST", `/step/model/${modelId}`, { fixture: "model/getModel" })
            .as("getModel");
        cy.intercept("GET", `/model/${modelId}/logs`, [])
            .as("getLogs");
        cy.visit(`project/${projectId}/model/${modelId}`);
    });

    it("displays an error message when an unsupported file name is uploaded", () => {
        cy.getBySel("upload-file-btn").scrollIntoView();
        cy.getBySel("upload-file-btn").selectFile("test/cypress/fixtures/files/flip.py", { action: "drag-drop" });

        cy.contains("This file name is not supported as it's reserved by FLIP.").should("be.visible");
    });

    it("displays uploaded status when model file is uploaded successfully", () => {
        cy.intercept("POST", `/files/preSignedUrl/model/${modelId}`, presignedPolicy)
            .as("fileLambda");
        cy.intercept(
            "POST",
            `${uploadOrigin}/`,
            { statusCode: 204 }
        ).as("fileUpload");

        cy.getBySel("upload-file-btn").scrollIntoView();
        cy.getBySel("upload-file-btn").selectFile("test/cypress/fixtures/files/trainer.py", { action: "drag-drop" });

        cy.getBySel("file-upload-status-scanning").should("be.visible");

        cy.intercept("POST", `/step/model/${modelId}`, { fixture: "model/getModelFileCompleted" })
            .as("getModelFile")
            .wait("@getModelFile", { requestTimeout: 20000 });

        cy.getBySel("file-upload-status-completed").should("be.visible");
    });

    it("displays scanning status", () => {
        cy.intercept("POST", `/files/preSignedUrl/model/${modelId}`, presignedPolicy)
            .as("fileLambda");
        cy.intercept(
            "POST",
            `${uploadOrigin}/`,
            { statusCode: 204 }
        ).as("fileUpload");

        cy.getBySel("upload-file-btn").scrollIntoView();
        cy.getBySel("upload-file-btn").selectFile("test/cypress/fixtures/files/trainer.py", { action: "drag-drop" });

        cy.getBySel("file-upload-status-scanning").should("be.visible");
    });

    // The two error-path tests below upload trainer.py rather than flip.py
    // — flip.py is in the BLACKLISTED_MODEL_FILES list so the page rejects
    // it client-side, before either the preSignedUrl POST or the /step/model
    // POST has a chance to surface their error responses.
    it("handles error from preSignedUrl call and displays error status", () => {
        cy.intercept("POST", `/files/preSignedUrl/model/${modelId}`, { statusCode: 500 })
            .as("getUploadURL");

        cy.getBySel("upload-file-btn").scrollIntoView();
        cy.getBySel("upload-file-btn").selectFile("test/cypress/fixtures/files/trainer.py", { action: "drag-drop" });

        cy.getBySel("file-upload-status-error").should("be.visible");
    });

    it("handles error from get model call and displays error status", () => {
        cy.intercept("POST", `/files/preSignedUrl/model/${modelId}`, presignedPolicy)
            .as("getUploadURL");

        cy.intercept(
            "POST",
            `${uploadOrigin}/`,
            { statusCode: 204 }
        ).as("fileUpload");

        cy.getBySel("upload-file-btn").scrollIntoView();
        cy.getBySel("upload-file-btn").selectFile("test/cypress/fixtures/files/trainer.py", { action: "drag-drop" });

        cy.getBySel("file-upload-status-scanning").should("be.visible");

        cy.intercept("POST", `/step/model/${modelId}`, { fixture: "model/getModelFileError" })
            .as("getModelFile")
            .wait("@getModelFile", { requestTimeout: 20000 });

        cy.getBySel("file-upload-status-error").should("be.visible");
    });
});
