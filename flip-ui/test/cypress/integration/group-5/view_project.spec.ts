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




import { validProject } from "../common";

describe("Project Page: STAGED", () => {
    before(() => {
        cy.login();
    });

    beforeEach(() => {
        cy.restoreLocalStorage();
        cy.intercept("GET", "/projects/*/image/status", { fixture: "project/getProjectStatus" })
            .as("getImagingProjectsStatus");

        cy.intercept("GET",
            "/projects/" + validProject.id,
            { fixture: "project/getStagedProject" }
        ).as("projectWithQuery");

        cy.intercept("GET", "/users/*/permissions", { fixture: "user/getPermissions" })
            .as("getAdminPermissions");

        cy.intercept("POST", "/projects/*/unstage", { statusCode: 200 })
            .as("unstageProject");

        cy.visit("/project/" + validProject.id);
    });

    it("can unstage a staged project", () => {

        cy.getBySel("unstage-project-btn").click();
        cy.getBySel("confirm-modal-btn").dblclick();

        cy.wait("@unstageProject");

        cy.get("@unstageProject.all").should("have.length", 1);
        cy.contains("The project has been unstaged.").should("be.visible").should("have.length", 1);
    });

    it("handles error on unstaging a project", () => {
        cy.intercept("POST", "/projects/*/unstage", { statusCode: 500 })
            .as("unstageProject");

        cy.getBySel("unstage-project-btn").click();
        cy.getBySel("confirm-modal-btn").click();

        cy.contains("There was a problem unstaging this project, please try again.").should("be.visible");
    });

    it("Allows the user to delete a staged project", () => {
        cy.intercept("DELETE", `/projects/${validProject.id}`, {
            statusCode: 200,
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

        cy.contains("Project deleted").should("be.visible");

        cy.url().should("include", "projects");
    });

    it("Shows the correct error message when the API for deleting a staged project returns an error", () => {
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

    it("displays an error message if a minimum of one trust is not selected for approval", () => {
        cy.getBySel("approve-project-btn").click();
        cy.contains("You must select a minimum of one trust when approving.")
            .should("be.visible");

        cy.getBySel("trust-staged-0").click();
        cy.contains("You must select a minimum of one trust when approving.")
            .should("not.exist");
    });

    it("successfully approves the project", () => {
        cy.intercept("POST", `/step/project/${validProject.id}/approve`, {
            statusCode: 200,
            body: [
                {
                    id: "SOMEIDFORKCH",
                    name: "Kings College Hospital",
                    endpoint: "localhost"
                },
                {
                    id: "SOMEIDFORUCLH",
                    name: "University College London Hospitals",
                    endpoint: "localhost"
                }
            ]
        }).as("approveProject");

        cy.getBySel("approve-project-btn").click();
        cy.contains("You must select a minimum of one trust when approving.")
            .should("be.visible");

        cy.getBySel("trust-staged-0").click();
        cy.contains("You must select a minimum of one trust when approving.")
            .should("not.exist");
        cy.getBySel("trust-staged-1").click();

        cy.getBySel("approve-project-btn").click();

        cy.wait("@approveProject");

        cy.contains("Project Approved")
            .should("be.visible");

        cy.get("@approveProject").its("request.body").should("deep.equal", { "trusts": ["SOMEIDFORKCH", "SOMEIDFORUCLH"] });
    });
});

describe("Project Page: STAGED with only one trust", () => {
    before(() => {
        cy.login();
    });

    beforeEach(() => {
        cy.restoreLocalStorage();
        cy.intercept("GET", "/projects/*/image/status", { fixture: "project/getProjectStatus" })
            .as("getImagingProjectsStatus");

        cy.intercept("GET",
            "/projects/" + validProject.id,
            { fixture: "project/getStagedProjectOneTrust" }
        ).as("projectWithQuery");

        cy.intercept("GET", "/users/*/permissions", { fixture: "user/getPermissions" })
            .as("getAdminPermissions");

        cy.intercept("POST", "/projects/*/unstage", { statusCode: 200 })
            .as("unstageProject");

        cy.visit("/project/" + validProject.id);
    });

    it("successfully approves the project", () => {
        cy.intercept("POST", `/step/project/${validProject.id}/approve`, {
            statusCode: 200,
            body: [{
                id: "SOMEIDFORKCH",
                name: "Kings College Hospital",
                endpoint: "localhost"
            }]
        }).as("approveProject");

        cy.getBySel("approve-project-btn").click();
        cy.contains("You must select a minimum of one trust when approving.")
            .should("be.visible");

        cy.getBySel("trust-staged-0").click();
        cy.contains("You must select a minimum of one trust when approving.")
            .should("not.exist");

        cy.getBySel("approve-project-btn").click();

        cy.wait("@approveProject");

        cy.contains("Project Approved")
            .should("be.visible");

        cy.get("@approveProject").its("request.body").should("deep.equal", { "trusts": ["SOMEIDFORKCH"] });
    });
});

describe("Project Page: Researcher & Owner [APPROVED]", () => {
    before(() => {
        cy.login();
    });

    beforeEach(() => {
        cy.restoreLocalStorage();
        cy.intercept("GET", "/projects/" + validProject.id + "/models*", { fixture: "model/getModels" })
            .as("getModels");

        cy.intercept("GET", "/projects/*/image/status", { fixture: "project/getProjectStatus" })
            .as("getImagingProjectsStatus");

        cy.intercept("GET", "/step/model/*", { fixture: "model/getModel" })
            .as("getModel");

        cy.intercept("GET", "/projects/" + validProject.id, { fixture: "project/getApprovedProject" })
            .as("getProject");

        cy.visit("/project/" + validProject.id);

        cy.wait("@getProject");
        cy.wait("@getImagingProjectsStatus");
    });

    it("Shows the models list", () => {
        cy.getBySel("models-unapproved-status").should("not.exist");
        cy.getBySel("models-approved-status").should("exist");
    });

    it("Shows cohort query ", () => {
        cy.getBySel("cohort-query-exists")
            .should("exist");
    });

    it("Cant be staged", () => {
        cy.getBySel("stage-project-btn").should("not.exist");
    });

    it("shows the imaging status", () => {
        cy.getBySel("trust-name-7e51a830-7b09-4bf7-b91a-0b4e1c36d3b2").contains("KCH");
        cy.getBySel("trust-name-5d512a2b-747e-4b1f-ad9d-f65fdb3c6585").contains("GSTT");
        cy.getBySel("trust-name-df8f0069-ad2c-44a7-b082-10e84d453b24").contains("UCLH");
        cy.getBySel("project-creation-complete-7e51a830-7b09-4bf7-b91a-0b4e1c36d3b2").should("exist");
        cy.getBySel("project-creation-complete-5d512a2b-747e-4b1f-ad9d-f65fdb3c6585").should("not.exist");
        cy.getBySel("project-creation-complete-df8f0069-ad2c-44a7-b082-10e84d453b24").should("exist");
        cy.getBySel("project-creation-incomplete-7e51a830-7b09-4bf7-b91a-0b4e1c36d3b2").should("not.exist");
        cy.getBySel("project-creation-incomplete-5d512a2b-747e-4b1f-ad9d-f65fdb3c6585").should("exist");
        cy.getBySel("project-creation-incomplete-df8f0069-ad2c-44a7-b082-10e84d453b24").should("not.exist");

        cy.getBySel("successful-imports-7e51a830-7b09-4bf7-b91a-0b4e1c36d3b2")
            .should("not.exist");
        cy.getBySel("successful-imports-5d512a2b-747e-4b1f-ad9d-f65fdb3c6585")
            .should("not.exist");
        cy.getBySel("successful-imports-df8f0069-ad2c-44a7-b082-10e84d453b24")
            .should("exist")
            .contains(12);

        cy.getBySel("processing-imports-7e51a830-7b09-4bf7-b91a-0b4e1c36d3b2")
            .should("not.exist");
        cy.getBySel("processing-imports-5d512a2b-747e-4b1f-ad9d-f65fdb3c6585")
            .should("not.exist");
        cy.getBySel("processing-imports-df8f0069-ad2c-44a7-b082-10e84d453b24")
            .should("exist")
            .contains(2);

        cy.getBySel("queued-imports-7e51a830-7b09-4bf7-b91a-0b4e1c36d3b2")
            .should("not.exist");
        cy.getBySel("queued-imports-5d512a2b-747e-4b1f-ad9d-f65fdb3c6585")
            .should("not.exist");
        cy.getBySel("queued-imports-df8f0069-ad2c-44a7-b082-10e84d453b24")
            .should("exist")
            .contains(212);

        cy.getBySel("failed-imports-7e51a830-7b09-4bf7-b91a-0b4e1c36d3b2")
            .should("not.exist");
        cy.getBySel("failed-imports-5d512a2b-747e-4b1f-ad9d-f65fdb3c6585")
            .should("not.exist");
        cy.getBySel("failed-imports-df8f0069-ad2c-44a7-b082-10e84d453b24")
            .should("exist")
            .contains(26);

        cy.getBySel("project-reimport-status-7e51a830-7b09-4bf7-b91a-0b4e1c36d3b2")
            .should("exist")
            .contains("2 / 5")
            .click()
            .get("[data-tippy-root]")
            .should("exist")
            .contains("Reimport attempts");

        cy.getBySel("import-status-warning-7e51a830-7b09-4bf7-b91a-0b4e1c36d3b2")
            .should("exist")
            .contains("Something went wrong when retrieving the study import status.");

        cy.getBySel("project-reimport-status-5d512a2b-747e-4b1f-ad9d-f65fdb3c6585")
            .should("not.exist");

        cy.getBySel("project-reimport-status-df8f0069-ad2c-44a7-b082-10e84d453b24")
            .should("exist")
            .contains("5 / 5")
            .click()
            .get("[data-tippy-root]")
            .should("exist")
            .contains("The max reimport count has been reached. Any failed studies will not be reimported. Please contact an XNAT administrator for assistance.");

        cy.getBySel("trust-name-7e51a830-7b09-4bf7-b91a-0b4e1c36d3b2").should("exist");
        cy.getBySel("trust-name-5d512a2b-747e-4b1f-ad9d-f65fdb3c6585").should("exist");
        cy.getBySel("trust-name-df8f0069-ad2c-44a7-b082-10e84d453b24").should("exist");
        cy.getBySel("overview-project-creation").contains("2/3");
        cy.getBySel("overview-image-retrieval").contains("12");
        cy.getBySel("filter-project-status").type("UCLH", { force: true });
        cy.getBySel("trust-name-7e51a830-7b09-4bf7-b91a-0b4e1c36d3b2").should("not.exist");
        cy.getBySel("trust-name-5d512a2b-747e-4b1f-ad9d-f65fdb3c6585").should("not.exist");
        cy.getBySel("trust-name-df8f0069-ad2c-44a7-b082-10e84d453b24").should("exist");
        cy.getBySel("overview-project-creation").contains("2/3");
        cy.getBySel("overview-image-retrieval").contains("12");

        cy.getBySel("filter-project-status").clear().type("GARBAGE");
        cy.getBySel("no-project-status-message").should("exist");
    });

    it("Can't be edited", () => {
        cy.getBySel("edit-project-btn").click();
        cy.getBySel("update-project-btn").should("not.exist");
    });

    it("Allows the user to delete an approved project", () => {
        cy.intercept("DELETE", `/projects/${validProject.id}`, {
            statusCode: 200,
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

        cy.contains("Project deleted").should("be.visible");

        cy.url().should("include", "projects");
    });

    it("Shows the correct error message when the API for deleting an approved project returns an error", () => {
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
});
