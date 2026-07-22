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

// Demo segment 4 — with imaging imported, the researcher creates a model,
// uploads the training application (real presigned-URL uploads to S3) and
// initiates federated training. The orchestrator waits for training to
// produce its first metrics off-camera afterwards.

import { requireEnv, saveDemoState } from "./support/demoFlow";

describe("FLIP demo — create model and start training", () => {
    it("creates the model, uploads the app files and initiates training", () => {
        const email = requireEnv("DEMO_RESEARCHER_EMAIL");
        const password = requireEnv("DEMO_RESEARCHER_PASSWORD");
        const projectId = requireEnv("DEMO_PROJECT_ID");
        const modelName = requireEnv("DEMO_MODEL_NAME");
        const modelDescription = requireEnv("DEMO_MODEL_DESCRIPTION");
        const appDir = requireEnv("DEMO_APP_DIR");
        const appFiles = requireEnv("DEMO_APP_FILES")
            .split(",")
            .map((f) => f.trim())
            .filter(Boolean);
        const backendLabel = String(Cypress.env("DEMO_BACKEND_LABEL") || "NVFLARE");

        cy.demoLogin(email, password);
        cy.demoCaption("Imaging is in place — the researcher returns to the approved project", 1000);
        cy.visit(`/project/${projectId}`);
        cy.demoPause(1500);

        cy.demoCaption("Creating a model for this project", 600);
        cy.getBySel("add-model-btn", { timeout: 60000 }).first().demoClick();
        // Scope to the open modal — page content reuses some of these
        // data-test hooks (same trap as the Create Project modal).
        cy.get("[role=dialog]").within(() => {
            cy.getBySel("model-name").demoType(modelName);
            cy.getBySel("model-description").demoType(modelDescription, { delay: 15 });
            cy.getBySel("create-model-btn").demoClick();
        });

        cy.url({ timeout: 60000 })
            .should("match", /\/model\/[0-9a-f-]{36}$/)
            .then((url) => {
                const modelId = url.split("/").pop() as string;
                saveDemoState({
                    projectId,
                    modelId
                });
            });

        cy.demoCaption(`Uploading ${backendLabel} application bundle`, 1000);
        const filePaths = appFiles.map((name) => `${appDir}/${name}`);
        cy.getBySel("upload-file-btn").scrollIntoView();
        // The visible control is a drop zone; the real <input type=file
        // multiple> behind it is hidden, hence force.
        cy.getBySel("upload-file-input").selectFile(filePaths, { force: true });

        // Each file does the full presigned-POST → S3 → scan-grace →
        // process-scanned-file round trip before flipping to Completed.
        cy.getBySel("file-upload-status-completed", { timeout: 240000 }).should(
            "have.length",
            appFiles.length
        );
        cy.demoPause(1500);

        cy.demoCaption("Confirming data enrichment and selecting the trusts to train at", 800);
        cy.getBySel("data-enrichment-btn").scrollIntoView();
        cy.getBySel("data-enrichment-btn").demoClick();
        cy.get("[data-test^=\"trust-selection-\"]")
            .should("have.length.at.least", 1)
            .each(($el) => {
                cy.wrap($el).scrollIntoView();
                cy.wrap($el).demoClick();
            });

        cy.demoCaption("Initiating federated training", 600);
        cy.getBySel("initiate-training-btn", { timeout: 90000 })
            .scrollIntoView()
            .should("not.be.disabled");
        cy.getBySel("initiate-training-btn").demoClick();

        // Once the hub accepts the run the Initiate button is replaced by
        // the training actions (stop/download).
        cy.getBySel("stop-training-btn", { timeout: 90000 }).should("exist");
        cy.demoCaption("The job is queued to the FL network — each trust trains on its own data", 500);
        cy.demoPause(3000);
    });
});
