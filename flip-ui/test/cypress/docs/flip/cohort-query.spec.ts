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

// Records docs/source/assets/flip/cohort-query.gif.
// Mirrors integration/group-6/cohort/create_cohort.spec.ts (OMOP happy path).

const PROJECT_ID = "6fcbdd40-3675-45c9-899e-1a005e5245ba";

describe("docs: cohort query", () => {
    it("submits an OMOP cohort query and shows the results", () => {
        cy.login();
        cy.intercept("GET", `/projects/${PROJECT_ID}`, {
            fixture: "project/getProjectWithQueryNotApprovedUnstagedEmpty"
        });
        cy.intercept("POST", "/step/cohort", { fixture: "cohort/cohortQueryResponseOMOP" }).as("submitCohortQuery");
        cy.intercept("GET", "cohort/*", { fixture: "cohort/cohortQueryResultsOmop" }).as("cohortResults");

        cy.visit(`/project/${PROJECT_ID}/cohort-query`);
        cy.demoPause();

        cy.getBySel("cohort-query").first().demoType(
            "SELECT accession_id FROM radiology_occurrence WHERE modality = 'MR'"
        );
        cy.demoPause();

        cy.getBySel("view-cohort-query-results-btn").demoClick();
        cy.wait("@submitCohortQuery");
        cy.wait("@cohortResults");
        cy.demoPause(1500);
    });
});
