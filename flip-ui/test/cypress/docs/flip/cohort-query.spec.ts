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
// Mirrors the happy path in test/cypress/integration/group-6/cohort/create_cohort.spec.ts
// ("Should be able to create a Cohort and display it's results using OMOP").

describe("docs: cohort query", () => {
    it("builds and runs a cohort query", () => {
        const projectId = "6fcbdd40-3675-45c9-899e-1a005e5245ba";

        cy.login();
        // sendQuery() returns the raw POST body and runCohortQuery() reads
        // `response.trust` off it directly. The cohortQueryResponseOMOP fixture
        // wraps that payload in a stringified `body` envelope, so the success
        // branch would throw and surface an error snackbar instead. Mock the
        // shape the app actually consumes (ICohortQueryResponse).
        cy.intercept("POST", "/step/cohort", {
            statusCode: 200,
            body: {
                trust: [
                    { name: "UCLH", statusCode: 200 },
                    { name: "KCH", statusCode: 200 }
                ],
                queryId: "c9453df9-d228-4c9c-80dd-9a2e3c2ecf11"
            }
        }).as("submitCohortQuery");
        cy.intercept("GET", "cohort/*", { fixture: "cohort/cohortQueryResultsOmop" })
            .as("cohortResults");
        cy.intercept("GET", "/projects/" + projectId, {
            fixture: "project/getProjectWithQueryNotApprovedUnstagedEmpty"
        }).as("getProject");

        cy.visit(`/project/${projectId}/cohort-query`);
        cy.wait("@getProject");
        cy.demoPause();

        cy.getBySel("cohort-query")
            .first()
            .demoType("SELECT accession_id, year_of_birth FROM omop.person WHERE year_of_birth < 1980");
        cy.demoPause();

        cy.getBySel("view-cohort-query-results-btn").demoClick();
        cy.wait("@submitCohortQuery");

        // Assert the success snackbar before anything else — Snackbar.show
        // auto-dismisses after 6s, so a later cy.wait could outlast it.
        cy.contains("Cohort query has been sent to trusts and queued for processing").should("be.visible");
        cy.demoPause(1200);
    });
});
