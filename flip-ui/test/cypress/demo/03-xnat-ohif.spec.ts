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

// Demo segment 3 — inside one trust: the imported cohort has landed in XNAT,
// and a study is opened in the OHIF DICOM viewer. This segment runs with
// CYPRESS_BASE_URL pointed at the trust's XNAT (e.g. http://localhost:8104);
// the orchestrator resolves the XNAT project (matched on secondary_ID ==
// FLIP project id) and an experiment id before launching it. Navigating
// straight to /VIEWER/ keeps OHIF in the recorded tab — the XNAT UI's own
// launch link opens a new tab, which Cypress can't follow.

import { requireEnv } from "./support/demoFlow";

describe("FLIP demo — XNAT + OHIF at the trust", () => {
    // XNAT's Velocity pages and the OHIF bundle both throw benign script
    // errors that would otherwise fail the segment.
    Cypress.on("uncaught:exception", () => false);

    it("logs in to the trust XNAT and views an imported DICOM in OHIF", () => {
        const username = requireEnv("DEMO_XNAT_USERNAME");
        const password = requireEnv("DEMO_XNAT_PASSWORD");
        const xnatProjectId = requireEnv("DEMO_XNAT_PROJECT_ID");
        const xnatSubjectId = requireEnv("DEMO_XNAT_SUBJECT_ID");
        const xnatExperimentId = requireEnv("DEMO_XNAT_EXPERIMENT_ID");
        const xnatExperimentLabel = requireEnv("DEMO_XNAT_EXPERIMENT_LABEL");

        cy.visit("/");
        cy.demoCaption("Meanwhile, inside the trust: the cohort's imaging has been imported into XNAT", 1200);
        cy.get("form#login_form", { timeout: 60000 }).within(() => {
            cy.get("input[name='username']").demoType(username);
            cy.get("input[name='password']").demoType(password, { log: false });
        });
        cy.get("form#login_form [type='submit'], form#login_form button").first().demoClick();

        // XNAT's post-login landing page.
        cy.url({ timeout: 60000 }).should("not.include", "Login.vm");
        cy.demoPause(1500);

        cy.demoCaption("Each FLIP project maps to an XNAT project holding its imported studies", 600);
        cy.visit(
            "/app/action/DisplayItemAction/search_value/" +
                `${xnatProjectId}/search_element/xnat%3AprojectData/search_field/xnat%3AprojectData.ID`
        );
        cy.demoPause(3200);

        cy.demoCaption("Reviewing an imported study in the OHIF DICOM viewer", 600);
        // Same query-string shape the plugin's own "View Session" action
        // builds (see /scripts/xnat/plugin/ohif-viewer/viewer.js) — the
        // viewer rejects the URL without subjectId.
        cy.visit(
            `/VIEWER/?subjectId=${xnatSubjectId}&projectId=${xnatProjectId}` +
                `&experimentId=${xnatExperimentId}&experimentLabel=${encodeURIComponent(xnatExperimentLabel)}`
        );
        // OHIF pulls series metadata + pixel data before first render.
        cy.get("canvas", { timeout: 180000 }).should("be.visible");
        cy.demoPause(6000);
    });
});
