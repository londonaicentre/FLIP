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




import { getShortDateFromString } from "@/utils/helpers";

import { validProject, validProjectWithQuery } from "../../common";

describe("Project Page: Researcher & Owner [UNAPPROVED]", () => {

    before(() => {
        cy.login();
    });

    beforeEach(() => {
        cy.restoreLocalStorage();
        cy.intercept(
            "GET",
            "/users/*/permissions", {
            statusCode: 200,
            fixture: "user/getPermissionsResearcher"
        }
        ).as("getPermissions");

        cy.intercept(
            "GET",
            "/projects/*", {
            fixture: "project/getProject",
            statusCode: 200
        }
        ).as("getProject");

        cy.intercept("GET", "/users/validUser@gmail.com", { statusCode: 200 }).as("validUser");

        cy.intercept("PUT", `projects/${validProject.id}`, { statusCode: 200 }).as("updateProject");
        cy.intercept("DELETE", `projects/${validProject.id}`, {
            statusCode: 200,
            body: {}
        });

        cy.visit("/project/" + validProject.id);
    });

    it("Show the correct breadcrumbs", () => {
        cy.getBySel("parent-page-text")
            .first()
            .should("contain.text", "Projects");

        cy.getBySel("current-page-text")
            .should("contain.text", validProject.name);
    });

    it("Has the correct href attribute", () => {
        cy.getBySel("parent-page-text")
            .first()
            .should("contain.text", "Projects")
            .should("have.attr", "href", "/projects");
    });

    it("Show the correct details", () => {
        cy.getBySel("project-name-text")
            .should("contain.text", validProject.name);

        cy.getBySel("project-description-text")
            .should("contain.text", validProject.description);

        cy.getBySel("project-creator-text")
            .should("contain.text", validProject.owneremail);

        cy.getBySel("project-created-time")
            .should("contain.text", getShortDateFromString(validProject.creationtimestamp));
    });

    it("Must have Cohort to stage and allow user to create one", () => {
        cy.contains("A cohort query is required before staging a project");
    });

    it("Does not show the models list", () => {
        cy.contains("Project approval is required to view or create models");
    });

    it("Does not allow me to approve the project", () => {
        cy.getBySel("approve-project-btn")
            .should("not.exist");
    });

    it("Doesn't show the imaging project status if the project is not approved", () => {
        cy.getBySel("project-status-container").should("not.exist");
    });

    it("Shows the empty cohort query and lets you create one", () => {
        cy.getBySel("empty-cohort-query")
            .should("exist");

        cy.getBySel("create-query-btn")
            .should("exist");
    });

    it.skip("Allows the user to delete the project", () => {
        cy.getBySel("edit-project-btn").click();
        cy.getBySel("delete-project-btn").click();

        cy.contains("Any active training jobs performed on the models within the project will be stopped.")
            .should("be.visible");

        cy.getBySel("confirmation-input")
            .should("exist")
            .clear()
            .type(validProject.name);

        cy.getBySel("confirm-modal-btn").click();

        cy.contains("Project deleted").should("be.visible");

        cy.url().should("include", "projects");
    });

    it.skip("Shows the correct error message when the API for deleting the project returns an error", () => {
        cy.intercept("DELETE", `/projects/${validProject.id}`, {
            statusCode: 500,
            body: {}
        });

        cy.getBySel("edit-project-btn").click();
        cy.getBySel("delete-project-btn").click();

        cy.contains("Any active training jobs performed on the models within the project will be stopped.")
            .should("be.visible");

        cy.getBySel("confirmation-input")
            .should("exist")
            .clear()
            .type(validProject.name);

        cy.getBySel("confirm-modal-btn").click();

        cy.contains("Unable to delete this project").should("be.visible");

        cy.url().should("include", `project/${validProject.id}`);
    });

    describe("With cohort query", () => {
        beforeEach(() => {
            cy.intercept(
                "GET",
                `/projects/${validProjectWithQuery.id}`, {
                fixture: "project/getProjectWithQueryNotApprovedUnstaged",
                statusCode: 200
            }
            ).as("getProject");
            cy.intercept("POST", `/projects/${validProjectWithQuery.id}/stage`, { statusCode: 200 }).as("stage");
            cy.visit(`/project/${validProjectWithQuery.id}`);
        });

        it.only("can stage with only one trust associated with a project", () => {

            cy.getBySel("stage-project-btn").click();
            cy.contains("You must select a minimum of one trust when staging.");

            cy.getBySel("KCH-selector").click();

            cy.getBySel("stage-project-btn").click();

            cy.wait("@stage");
            cy.get("@stage.all").should("have.length", 1).then(console.log);

            cy.get("@stage").its("request.body").should("deep.equal", {
                "trusts": [
                    "4c9692ac-f607-4216-9f0b-b45eb72d83d2"
                ]
            });
        });

        it("can stage with all trusts associated with a project", () => {

            cy.getBySel("stage-project-btn").click();
            cy.contains("You must select a minimum of one trust when staging.");

            cy.getBySel("KCH-selector").click();
            cy.getBySel("UCLH-selector").click();

            cy.getBySel("stage-project-btn").click();

            cy.wait("@stage");
            cy.get("@stage.all").should("have.length", 1).then(console.log);

            cy.get("@stage").its("request.body").should("deep.equal", {
                "trusts": [
                    "4c9692ac-f607-4216-9f0b-b45eb72d83d2",
                    "53ca8126-5551-41a8-bd0a-587956c859d5"
                ]
            });
        });
    });
});

describe("Editing Project", () => {
    before(() => {
        cy.login();
    });

    beforeEach(() => {
        cy.restoreLocalStorage();
        cy.intercept(
            "GET",
            "/users/*/permissions", {
            fixture: "user/getPermissionsResearcher",
            statusCode: 200
        }
        ).as("getPermissions");

        cy.intercept(
            "GET",
            "/projects/*", {
            fixture: "project/getProject",
            statusCode: 200
        }
        ).as("getProject");

        cy.intercept("GET", "users/validUser@gmail.com", { statusCode: 200 }).as("validUser");
        cy.intercept("PUT", `projects/${validProject.id}`, { statusCode: 200 }).as("updateProject");

        cy.visit("/project/" + validProject.id);
    });

    it.skip("Handles project editing", () => {
        cy.getBySel("edit-project-btn").click();
        cy.getBySel("added-user-0").should("contain.text", validProject.users[0].email);
        cy.getBySel("added-user-1").should("contain.text", validProject.users[1].email);


        cy.getBySel("add-user-project-input").clear().type("validUser@gmail.com");
        cy.getBySel("add-user-project-btn").click();


        cy.intercept("GET", "/users/invalidUser@gmail.com", { statusCode: 404 }).as("invalidUser");
        cy.getBySel("add-user-project-input").clear().type("invalidUser@gmail.com");
        cy.getBySel("add-user-project-btn").click();
        cy.wait("@invalidUser");
        cy.contains("invalidUser@gmail.com cannot be found");


        cy.intercept("PUT", `projects/${validProject.id}`, { statusCode: 200 }).as("updateProject");
        cy.getBySel("remove-user-0-project-btn").click();
        cy.wait("@updateProject");

        cy.getBySel("edit-project-btn").click();
        cy.intercept("PUT", `/projects/${validProject.id}`, { statusCode: 500 });
        cy.getBySel("remove-user-1-project-btn").click();
        cy.contains("Something went wrong, please try again later. If the issue persists please contact the service desk.");

        cy.getBySel("edit-project-btn").click({ force: true });
        cy.intercept("PUT", `/projects/${validProject.id}`, {
            statusCode: 200,
            body: {}
        });

        cy.getBySel("project-name")
            .clear()
            .type("Updated Project Name");
        cy.getBySel("project-description")
            .clear()
            .type("Updated Project Description");

        cy.getBySel("update-project-btn").click();

        cy.contains("This project has been updated.").should("be.visible");

        cy.intercept("PUT", `/projects/${validProject.id}`, {
            statusCode: 500,
            body: {}
        });

        cy.getBySel("edit-project-btn").click({ force: true });

        cy.getBySel("project-name")
            .clear()
            .type("Updated Project Name");
        cy.getBySel("project-description")
            .clear()
            .type("Updated Project Description");

        cy.getBySel("update-project-btn").click();

        cy.contains("Unable to update project").should("be.visible");

        cy.intercept("DELETE", `/projects/${validProject.id}`, {
            statusCode: 200,
            body: {}
        });

        cy.getBySel("edit-project-btn").click({ force: true });

        cy.getBySel("delete-project-btn")
            .click();

        cy.getBySel("confirmation-input").clear().type(validProject.name);

        cy.getBySel("confirm-modal-btn").click();

        cy.contains("Project deleted").should("be.visible");

        cy.url().should("include", "/projects");
    });
});
