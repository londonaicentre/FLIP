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

// Demo segment 6 — training has finished (the orchestrator waited for
// RESULTS_UPLOADED): the researcher downloads the aggregated results via
// the presigned-URL flow. The download itself lands in the Cypress
// downloads folder; on camera the click + snackbar-free happy path is the
// story.

import { demoVisit, requireEnv, revealDemo } from "./support/demoFlow";

describe("FLIP demo — download results", () => {
    it("downloads the federated training results", () => {
        const email = requireEnv("DEMO_RESEARCHER_EMAIL");
        const password = requireEnv("DEMO_RESEARCHER_PASSWORD");
        const projectId = requireEnv("DEMO_PROJECT_ID");
        const modelId = requireEnv("DEMO_MODEL_ID");

        // Stealth sign-in — the clip opens directly on the finished dashboard.
        cy.demoLogin(email, password, { stealth: true });
        demoVisit(`/project/${projectId}/model/${modelId}`);
        cy.getBySel("training-timeline", { timeout: 60000 }).should("exist");
        cy.getBySel("download-results-btn", { timeout: 120000 }).should("exist");
        revealDemo();

        cy.demoCaption("Training complete — results are uploaded from the FL network", 1200);
        cy.getBySel("training-timeline").should("be.visible");
        cy.demoPause(2000);

        cy.demoCaption("Downloading the aggregated global model", 800);
        cy.getBySel("download-results-btn", { timeout: 120000 }).should("not.be.disabled");
        cy.getBySel("download-results-btn").demoClick();
        cy.demoPause(3500);

        cy.demoCaption("A model trained across every hospital — while the data never left any of them", 500);
        cy.demoPause(4000);
    });
});
