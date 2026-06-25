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

// Records docs/source/assets/flip/filter-project.gif.
// Mirrors the happy path in test/cypress/integration/group-6/project_list.spec.ts
// ("displays only the search results when a search term is entered").

describe("docs: filter project list", () => {
    it("filters the project list by a search term", () => {
        cy.login();
        cy.intercept("GET", "/projects?pageNumber=1&pageSize=20", { fixture: "project/getProjects" })
            .as("getProjects");
        cy.intercept("GET", "/projects?pageNumber=1&pageSize=20&search=Example", {
            fixture: "project/getProjectsSearch"
        }).as("getProjectsSearch");

        cy.visit("/projects");
        cy.wait("@getProjects");
        cy.demoPause();

        cy.getBySel("project-search").demoType("Example");
        cy.wait("@getProjectsSearch");
        cy.demoPause();

        cy.contains("Example project").should("be.visible");
        cy.demoPause(1200);
    });
});
