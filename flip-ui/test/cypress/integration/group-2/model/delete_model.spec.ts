/*
 * Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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




import { validModel, validProject } from "../../common";

describe.skip("Delete Model: Researcher & Owner", () => {
    before(() => {
        cy.login();
    });

    beforeEach(() => {
        cy.restoreLocalStorage();

        cy.intercept("GET", "/users/*/permissions", { fixture: "user/getPermissionsResearcher" })
            .as("getPermissions");
        cy.intercept("GET", "/projects/*", { fixture: "project/getApprovedProject" })
            .as("getProject");
        cy.intercept("POST", `/step/model/${validModel.id}`, { fixture: "model/getModel" })
            .as("getModel");
        cy.intercept("GET", `/model/${validModel.id}/logs`, [])
            .as("getLogs");
        cy.visit(`project/${validProject.id}/model/${validModel.id}`);
        cy.wait("@getModel");

        cy.getBySel("edit-model-btn")
            .click();
    });

    it("Allows the user to delete the model", () => {
        cy.intercept("DELETE", `/model/${validModel.id}`, {
            statusCode: 200,
            body: {}
        });

        cy.getBySel("delete-model-btn")
            .should("exist")
            .click();

        cy.contains("Training for this model will also be stopped if active.")
            .should("be.visible");

        cy.getBySel("confirmation-input")
            .should("exist")
            .clear()
            .type(validModel.name);

        cy.getBySel("confirm-modal-btn").click();

        cy.contains("Model deleted").should("be.visible");

        cy.url().should("include", `project/${validProject.id}`);
    });

    it("Shows the correct error message when the API returns an error", () => {
        cy.intercept("DELETE", `/model/${validModel.id}`, {
            statusCode: 500,
            body: {}
        }).as("deleteModel");

        cy.getBySel("delete-model-btn")
            .should("exist")
            .click();

        cy.getBySel("confirmation-input")
            .should("exist")
            .clear()
            .type(validModel.name);

        cy.getBySel("confirm-modal-btn").click();

        cy.wait("@deleteModel");

        cy.contains("Unable to delete this model").should("be.visible");

        cy.url().should("include", `project/${validProject.id}/model/${validModel.id}`);
    });
});

describe("Delete Model: With Permissions", () => {
    before(() => {
        cy.login();
    });

    beforeEach(() => {
        cy.restoreLocalStorage();

        cy.intercept("GET", "/users/*/permissions", { fixture: "user/getPermissions" })
            .as("getPermissions");
        cy.intercept("GET", "/projects/*", { fixture: "project/getApprovedProject" })
            .as("getProject");
        cy.intercept("POST", `/step/model/${validModel.id}`, { fixture: "model/getModel" })
            .as("getModel");
        cy.intercept("GET", `/model/${validModel.id}/logs`, [])
            .as("getLogs");
        cy.intercept(
            "GET",
            "/projects/*/models?pageSize=5", {
            fixture: "model/getLatestModels",
            statusCode: 200
        }
        ).as("getLatestModels");
        cy.visit(`project/${validProject.id}/model/${validModel.id}`);
        cy.wait("@getModel");

        cy.getBySel("edit-model-btn")
            .click();
    });
    it("Allows the user to delete the model", () => {
        cy.intercept("DELETE", `/model/${validModel.id}`, {
            statusCode: 200,
            body: {}
        });

        cy.getBySel("delete-model-btn")
            .should("exist")
            .click();

        cy.contains("Training for this model will also be stopped if active.")
            .should("be.visible");

        cy.getBySel("confirmation-input")
            .should("exist")
            .clear()
            .type(validModel.name);

        cy.getBySel("confirm-modal-btn").click();

        cy.contains("Model deleted").should("be.visible");

        cy.url().should("include", `project/${validProject.id}`);
    });

    it("Shows the correct error message when the API returns an error", () => {
        cy.intercept("DELETE", `/model/${validModel.id}`, {
            statusCode: 500,
            body: {}
        });

        cy.getBySel("delete-model-btn")
            .should("exist")
            .click();

        cy.getBySel("confirmation-input")
            .should("exist")
            .clear()
            .type(validModel.name);

        cy.getBySel("confirm-modal-btn").click();

        cy.contains("Unable to delete this model").should("be.visible");

        cy.url().should("include", `project/${validProject.id}/model/${validModel.id}`);
    });
});

describe.skip("Delete Model: User", () => {
    before(() => {
        cy.login();
    });

    beforeEach(() => {
        cy.restoreLocalStorage();

        cy.intercept("GET", "/users/*/permissions", { fixture: "user/getPermissionsResearcher" })
            .as("getPermissions");
        cy.intercept("GET", "/projects/*", { fixture: "project/getApprovedProjectUser" })
            .as("getProject");
        cy.intercept("POST", `/step/model/${validModel.id}`, { fixture: "model/getModel" })
            .as("getModel");
        cy.intercept("GET", `/model/${validModel.id}/logs`, [])
            .as("getLogs");
        cy.visit(`project/${validProject.id}/model/${validModel.id}`);
        cy.wait("@getModel");

        cy.getBySel("edit-model-btn")
            .click();
    });

    it("Allows the user to delete the model", () => {
        cy.intercept("DELETE", `/model/${validModel.id}`, {
            statusCode: 204,
            body: {}
        });

        cy.getBySel("delete-model-btn")
            .should("exist")
            .click();

        cy.contains("Training for this model will also be stopped if active.")
            .should("be.visible");

        cy.getBySel("confirmation-input")
            .should("exist")
            .clear()
            .type(validModel.name);

        cy.getBySel("confirm-modal-btn").click();

        cy.contains("Model deleted").should("be.visible");

        cy.url().should("include", `project/${validProject.id}`);
    });
});
