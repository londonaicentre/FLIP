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




import { validProjectWithQuery } from "../../common";

describe("Edit Cohort Query Page", () => {
    before(() => {
        cy.login();
    });

    beforeEach(() => {
        cy.restoreLocalStorage();
    });

    it("OMOP - Should allow the user to edit the query if the project is not approved", () => {
        cy.intercept("GET", "/projects/" + validProjectWithQuery.id, { fixture: "project/getProjectWithQueryNotApprovedUnstaged" })
            .as("getProject");
        cy.visit(`/project/${validProjectWithQuery.id}/cohort-query`);
        cy.intercept("POST", "/step/cohort", { fixture: "cohort/cohortQueryResponseOMOPEdit" })
            .as("submitCohortQuery");
        cy.intercept("GET", "cohort/*", { fixture: "cohort/cohortQueryResultsOmopEdit" })
            .as("cohortResults");

        cy.getBySel("cohort-query").first().type(" WHERE age < 20");

        cy.getBySel("view-cohort-query-results-btn").click();

        cy.wait("@submitCohortQuery");

        cy.get("@submitCohortQuery").its("request.body").should("deep.equal",
            {
                "name": validProjectWithQuery.name + ": Cohort Query",
                "query": "Some query > 3000 WHERE age < 20",
                "projectId": validProjectWithQuery.id
            });

        cy.wait("@cohortResults");
        cy.get("canvas").eq(0).scrollIntoView().should("be.visible");
        cy.get("canvas").eq(1).scrollIntoView().should("be.visible");
        cy.get("canvas").eq(2).scrollIntoView().should("be.visible");
    });

    it("Should prevent editing a cohort query for an approved project", () => {
        cy.intercept("GET", "/projects/" + validProjectWithQuery.id, { fixture: "project/getApprovedProject" })
            .as("getProject");
        cy.visit(`/project/${validProjectWithQuery.id}/cohort-query`);
        cy.wait("@getProject");

        cy.url().should("include", "/project/" + validProjectWithQuery.id);
        cy.contains("This query is now locked and can not be edited as the project has been staged.").should("be.visible");
    });
});
