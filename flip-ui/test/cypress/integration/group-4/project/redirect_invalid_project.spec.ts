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




describe("redirect user to the project list page if the given project does not exist", () => {
    const projectId = "13fe7e36-2310-432d-8c59-91da99b80988";

    before(() => {
        cy.login();
    });

    beforeEach(() => {
        cy.restoreLocalStorage();
    });

    it("redirects to project list on nav to non-existent project page", () => {
        cy.intercept("GET", `/projects/${projectId}`, { statusCode: 404 })
            .as("getProject404");

        cy.visit(`/project/${projectId}`);

        cy.wait("@getProject404");

        cy.url().should("include", "/projects");
        cy.contains("The requested project could not be found.").should("be.visible");
    });

    it("redirects to project list on nav to 'model list' of non-existent project", () => {
        cy.intercept("GET", `/projects/${projectId}`, { statusCode: 404 })
            .as("getProject404");

        cy.visit(`/project/${projectId}/models`);

        cy.wait("@getProject404");

        cy.url().should("include", "/projects");
        cy.contains("The requested project could not be found.").should("be.visible");
    });

    it("redirects to project list on nav to 'model dashboard' of non-existent project", () => {
        cy.fixture("model/getModel").then((getModel) => {
            cy.intercept("GET", `/projects/${getModel.projectId}`, { statusCode: 404 })
                .as("getProject404");

            cy.intercept("POST", `/step/model/${getModel.modelId}`, { fixture: "model/getModel" })
                .as("getModel");

            cy.visit(`project/${getModel.projectId}/model/${getModel.modelId}`);
        });
        cy.wait("@getProject404");

        cy.url().should("include", "/projects");
        cy.contains("The requested project could not be found.").should("be.visible");
    });

    it("redirects to project list on nav to 'create model' of non-existent project", () => {
        cy.intercept("GET", `/projects/${projectId}`, { statusCode: 404 })
            .as("getProject404");

        cy.visit(`/project/${projectId}/model/create`);

        cy.wait("@getProject404");

        cy.url().should("include", "/projects");
        cy.contains("The requested project could not be found.").should("be.visible");
    });

    it("redirects to project list on nav to 'create cohort' of non-existent project", () => {
        cy.intercept("GET", `/projects/${projectId}`, { statusCode: 404 })
            .as("getProject404");

        cy.visit(`/project/${projectId}/cohort-query`);

        cy.wait("@getProject404");

        cy.url().should("include", "/projects");
        cy.contains("The requested project could not be found.").should("be.visible");
    });

    it("redirects to project list on nav to page where project is not given or invalid", () => {
        cy.intercept("GET", `/projects/${projectId}`, { statusCode: 400 })
            .as("getProject400");

        cy.visit(`/project/${projectId}/models`);

        cy.wait("@getProject400");

        cy.url().should("include", "/projects");
        cy.contains("The requested project could not be found.").should("be.visible");
    });
});
