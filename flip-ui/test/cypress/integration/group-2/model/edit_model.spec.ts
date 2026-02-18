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




import { validModel, validProject } from "../../common";

describe.skip("edit model", () => {
    before(() => {
        cy.login();
    });

    beforeEach(() => {
        cy.restoreLocalStorage();

        cy.intercept("GET", "/users/*/permissions", { fixture: "user/getPermissionsResearcher" })
            .as("getPermissions");
        cy.intercept(
            "GET",
            "/projects/*",
            { fixture: "project/getApprovedProject" }
        ).as("getProject");

        cy.intercept("GET", `/model/${validModel.id}/logs`, [])
            .as("getLogs");
    });

    describe("model has not began training - status is PENDING", () => {
        beforeEach(() => {
            cy.intercept("POST", `/step/model/${validModel.id}`, { fixture: "model/getModel" })
                .as("getModel");

            cy.visit(`project/${validProject.id}/model/${validModel.id}`);
            cy.wait("@getModel");
        });

        it("Allows user to edit the model", () => {
            cy.intercept("PUT", "/model/*", {
                statusCode: 200,
                body: {}
            }).as("editModel");

            cy.getBySel("edit-model-btn").click();

            cy.getBySel("model-name")
                .clear()
                .type("Updated Model Name");
            cy.getBySel("model-description")
                .clear()
                .type("Updated Model Description");

            cy.getBySel("update-model-btn").click();

            cy.wait("@editModel").its("request.body").should("deep.equal", {
                "name": "Updated Model Name",
                "description": "Updated Model Description"
            });

            cy.contains("This model has been updated.").should("be.visible");
        });

        it("Shows the correct error messages", () => {
            cy.intercept("PUT", "/model/*", {
                statusCode: 500,
                body: {}
            }).as("editModel");

            cy.getBySel("edit-model-btn").click();

            cy.getBySel("model-name")
                .clear()
                .type("Updated Model Name");
            cy.getBySel("model-description")
                .clear()
                .type("Updated Model Description");

            cy.getBySel("update-model-btn").click();

            cy.wait("@editModel");

            cy.contains("Unable to update model").should("be.visible");
        });
    });

    describe("model which has began training - status is not PENDING", () => {
        beforeEach(() => {
            cy.intercept("POST", `/step/model/${validModel.id}`, { fixture: "model/getModelPostTraining" })
                .as("getModel");

            cy.visit(`project/${validProject.id}/model/${validModel.id}`);
            cy.wait("@getModel");
        });

        it("user is unable to edit model which has begun training", () => {
            cy.getBySel("edit-model-btn").click();

            cy.contains("This training has been started so you can no longer edit model details.").should("be.visible");

            cy.getBySel("model-name").should("be.disabled");
            cy.getBySel("model-description").should("be.disabled");
            cy.getBySel("update-model-btn").should("not.exist");
        });
    });
});
