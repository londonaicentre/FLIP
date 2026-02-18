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




import { validProject } from "../../common";

describe("model list", () => {
    const projectId = validProject.id;

    before(() => {
        cy.login();
    });

    beforeEach(() => {
        cy.restoreLocalStorage();
        cy.intercept("GET",
            "/projects/" + validProject.id,
            { fixture: "project/getProjectWithQuery" }
        ).as("projectWithQuery");
    });

    it("displays appropriate message when backend call fails", () => {
        cy.intercept("GET", `/projects/${projectId}/models?pageNumber=1&pageSize=20`, {
            statusCode: 500,
            body: {}
        }).as("getModels");
        cy.visit(`/project/${projectId}/models`);
        cy.wait("@getModels");

        cy.contains("Something went wrong");
    });

    it("displays appropriate message when get models returns nil", () => {
        cy.intercept("GET", `/projects/${projectId}/models?pageNumber=1&pageSize=20`, { fixture: "model/getModelsEmpty" })
            .as("getModels");
        cy.visit(`/project/${projectId}/models`);
        cy.wait("@getModels");

        cy.contains("There are no models to show").should("be.visible");
    });

    it("redirects to model list for the given project when 'View Models' is clicked", () => {
        cy.fixture("model/getModels").then((models) => {
            cy.intercept("GET", `/projects/${projectId}/models?pageNumber=1&pageSize=20`, models)
                .as("getModels");
            cy.visit(`/project/${projectId}/models`);
            cy.wait("@getModels");

            cy.getBySel("view-models-btn").first().click();

            cy.url().should("include", `/model/${models.data[0].id}`);
        });
    });

    it("paginates successfully when the number of models returned exceeds pageSize", () => {

        cy.intercept("GET", `/projects/${projectId}/models?pageNumber=1&pageSize=20`, { fixture: "model/getModels" })
            .as("pageOne");

        cy.visit(`/project/${projectId}/models`);
        cy.wait("@pageOne");

        cy.intercept("GET", `/projects/${projectId}/models?pageNumber=2&pageSize=20`, { fixture: "model/getModelsPageTwo" })
            .as("pageTwo");
        cy.getBySel("page-btn-2").click();
        cy.wait("@pageTwo");

        cy.contains("This model is on page 2").should("be.visible");
        cy.contains("This test model exists on page 1").should("not.exist");
    });

    it("scrolls to the final model list item", () => {
        cy.intercept("GET", `/projects/${projectId}/models?pageNumber=1&pageSize=20`, { fixture: "model/getModels" })
            .as("pageOne");

        cy.visit(`/project/${projectId}/models`);

        cy.wait("@pageOne");

        cy.getBySel("model-list-item-19").scrollIntoView().should("be.visible");
    });

    it("displays only the search results when a search term is entered", () => {

        cy.intercept("GET", `/projects/${projectId}/models?pageNumber=1&pageSize=20`, { fixture: "model/getModels" })
            .as("getModels");

        cy.visit(`/project/${projectId}/models`);
        cy.wait("@getModels");

        cy.intercept("GET", `/projects/${projectId}/models?pageNumber=1&pageSize=20&search=Example`, { fixture: "model/getModelsSearch" })
            .as("getModelsSearch");
        cy.getBySel("model-search").type("Example");
        cy.wait("@getModelsSearch");

        cy.contains("A model completed doing important things").should("not.exist");
        cy.contains("Example Model").should("be.visible");
    });

    it("displays all models after a search term has been removed", () => {

        cy.intercept("GET", `/projects/${projectId}/models?pageNumber=1&pageSize=20`, { fixture: "model/getModels" })
            .as("getModels");
        cy.visit(`/project/${projectId}/models`);
        cy.wait("@getModels");
        cy.intercept("GET", `/projects/${projectId}/models?pageNumber=1&pageSize=20&search=Example`, { fixture: "model/getModelsSearch" })
            .as("getModelsSearch");
        cy.getBySel("model-search").type("Example");
        cy.wait("@getModelsSearch");
        cy.getBySel("model-search").clear();

        // assert model which only exists in original getModels call and does not match search input
        cy.contains("This is a test model").should("be.visible");
    });
});
