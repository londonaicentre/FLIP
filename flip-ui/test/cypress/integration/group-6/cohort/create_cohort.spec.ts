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




import { validProject, validProjectWithQuery } from "../../common";

describe("Cohort Query Page - OMOP", () => {
    before(() => {
        cy.login();
    });

    beforeEach(() => {
        cy.restoreLocalStorage();
        cy.intercept("POST", "/step/cohort", { fixture: "cohort/cohortQueryResponseOMOP" })
            .as("submitCohortQuery");
        cy.intercept("GET", "cohort/*",
            { fixture: "cohort/cohortQueryResultsOmop" })
            .as("cohortResults");
        cy.intercept("GET", "/projects/" + validProjectWithQuery.id, { fixture: "project/getProjectWithQueryNotApprovedUnstagedEmpty" })
            .as("getProject");
        cy.visit(`/project/${validProject.id}/cohort-query`);
    });

    it("Should be able to create a Cohort and display it's results using OMOP", () => {

        cy.getBySel("cohort-query").first().invoke("val", "").type("select * from somewhere", {});

        cy.getBySel("view-cohort-query-results-btn").click();

        cy.wait("@submitCohortQuery");

        cy.get("@submitCohortQuery").its("request.body").should("deep.equal",
            {
                "name": validProject.name + ": Cohort Query",
                "query": "select * from somewhere",
                "projectId": validProject.id
            });

        cy.wait("@cohortResults");
        cy.get("canvas").eq(0).scrollIntoView().should("be.visible");
        cy.get("canvas").eq(1).scrollIntoView().should("be.visible");
        cy.get("canvas").eq(2).scrollIntoView().should("be.visible");
    });

    it("Should show a validation error on submit with no query body", () => {
        cy.getBySel("view-cohort-query-results-btn").click();

        cy.get(".error_message").should("have.text", "A query is required and can't be left blank");
    });

    it("Should show an error message when a trust does not respond with a 200", () => {
        cy.intercept("POST", "/step/cohort", { fixture: "cohort/invalidCohortQueryResponseOMOP" })
            .as("invalidSubmitCohortQuery");

        cy.getBySel("cohort-query").first().type("select * from somewhere");

        cy.getBySel("view-cohort-query-results-btn").click();

        cy.wait("@invalidSubmitCohortQuery");

        cy.get("@invalidSubmitCohortQuery").its("request.body").should("deep.equal",
            {
                "name": validProject.name + ": Cohort Query",
                "query": "select * from somewhere",
                "projectId": validProject.id
            });

        cy.getBySel("no-results-message").should("not.exist");
        cy.getBySel("snackbar-text").should("contain", "There was a problem running this cohort query.");
    });

    it("Should show a message when no results are returned", () => {
        cy.intercept("GET", "cohort/*",
            { body: { trustsResults: [] } })
            .as("emptyCohortResults");

        cy.getBySel("cohort-query").first().type("select * from somewhere");

        cy.getBySel("view-cohort-query-results-btn").click();

        cy.wait("@submitCohortQuery");

        cy.get("@submitCohortQuery").its("request.body").should("deep.equal",
            {
                "name": validProject.name + ": Cohort Query",
                "query": "select * from somewhere",
                "projectId": validProject.id
            });

        cy.wait("@emptyCohortResults");

        cy.getBySel("no-results-message").should("contain", "No results to show");
    });
});
