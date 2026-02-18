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

describe("create model", () => {
    before(() => {
        cy.login();
    });

    beforeEach(() => {
        cy.restoreLocalStorage();
        cy.intercept("GET", `/projects/${validProjectWithQuery.id}`, { fixture: "project/getProjectWithQuery" });
        cy.intercept("GET", "/projects/" + validProjectWithQuery.id + "/models*", { fixture: "model/getModels" })
            .as("getModels");
        cy.visit(`/project/${validProjectWithQuery.id}`);
        cy.wait("@getModels");
        cy.getBySel("add-model-btn").first().click();
    });

    it("when name and description are provided successfully, model is created", () => {

        const modelId = validProjectWithQuery.id;
        cy.intercept("POST", "/model", {
            statusCode: 200,
            body: { "id": modelId }
        }).as("createModel");

        cy.intercept("POST", "/step/model/" + modelId, { fixture: "model/getModel" })
            .as("getModel");

        cy.getBySel("model-name").type("Model Name Test");
        cy.getBySel("model-description").type("Model Description Test");
        cy.getBySel("create-model-btn").first().click();
        cy.wait("@createModel");
        cy.get("@createModel").its("request.body").should("deep.equal", {
            "name": "Model Name Test",
            "description": "Model Description Test",
            "projectId": validProjectWithQuery.id
        });
        cy.contains("Model created successfully").should("be.visible");
        cy.url().should("include", `/model/${modelId}`);
    });

    it("displays validation if backend calls fail", () => {
        cy.intercept("POST", "/model", {
            statusCode: 500,
            body: {}
        });

        cy.getBySel("model-name").type("Model Name Test");
        cy.getBySel("model-description").type("Model Description Test");
        cy.getBySel("create-model-btn").first().click();
        cy.contains("There was a problem creating this model.").should("be.visible");
        cy.url().should("include", `/project/${validProjectWithQuery.id}`);
    });

    it("displays validation when name is not provided", () => {
        cy.getBySel("model-description").type("Model Description Test");
        cy.getBySel("create-model-btn").first().click();
        cy.contains("A model name is required and can't be left blank.").should("be.visible");
        cy.url().should("include", `/project/${validProjectWithQuery.id}`);
    });

    it("displays validation messages when name and description are too long", () => {
        cy.getBySel("model-name")
            .type("Lorem ipsum dolor sit amet, consectetuer adipiscing elit. Aenean commodo ligula eget dolor. Aenean m");
        cy.contains("Model name must not be longer than 75 characters.")
            .should("be.visible");

        cy.getBySel("model-description")
            .type(
                `Lorem ipsum dolor sit amet, consectetuer adipiscing elit.
                Aenean commodo ligula eget dolor. Aenean massa.
                Cum sociis natoque penatibus et magnis dis parturient montes, nascetur ridiculus mus.
                Donec quam felis, ultricies nec, pellentesque eu, pretium quis, sem.
                Nulla consequat massa quis enim. Donec.`,
                { delay: 0 }
            );
        cy.contains("Model description must not be longer than 250 characters.")
            .should("be.visible");
    });
});
