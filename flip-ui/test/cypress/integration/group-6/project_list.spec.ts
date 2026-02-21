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




describe("project list", () => {
    before(() => {
        cy.login();
    });

    beforeEach(() => {
        cy.restoreLocalStorage();
        cy.intercept("GET", "/projects?pageNumber=1&pageSize=20", {
            statusCode: 500,
            body: {}
        }).as("getProjectsError");
    });

    it("displays appropriate message when backend call fails", () => {
        cy.visit("/projects");

        cy.wait("@getProjectsError");

        cy.contains("Something went wrong");
    });

    it("displays appropriate state of the project", () => {
        cy.intercept("GET", "/projects?pageNumber=1&pageSize=20", { fixture: "project/getProjects" })
            .as("getProjects");
        cy.visit("/projects");
        cy.wait("@getProjects");
        cy.getBySel("project-list-item-1").getBySel("project-status-indicator").should("contain", "STAGED");
        cy.getBySel("project-list-item-2").getBySel("project-status-indicator").should("contain", "APPROVED");
    });

    it("displays appropriate message when get projects returns nil", () => {
        cy.intercept("GET", "/projects?pageNumber=1&pageSize=20", { fixture: "project/getProjectsEmpty" })
            .as("getProjectsEmpty");

        cy.visit("/projects");

        cy.wait("@getProjectsEmpty");

        cy.contains("There are no projects to show").should("be.visible");
    });

    it("redirects to the project page when 'View Project' is clicked", () => {
        cy.fixture("project/getProjects").then((projects) => {
            cy.intercept("GET", "/projects?pageNumber=1&pageSize=20", projects)
                .as("getProjects");

            cy.intercept("GET", "/projects/" + projects.data[0].id, { fixture: "project/getProject" })
                .as("getProject");

            cy.visit("/projects");

            cy.wait("@getProjects");

            cy.getBySel("view-project-btn").first().click();

            cy.url().should("include", `/project/${projects.data[0].id}`);
        });
    });

    it("paginates successfully when the number of projects returned exceeds pageSize", () => {
        cy.intercept("GET", "/projects?pageNumber=1&pageSize=20", { fixture: "project/getProjects" })
            .as("pageOne");
        cy.visit("/projects");
        cy.wait("@pageOne");

        cy.intercept("GET", "/projects?pageNumber=2&pageSize=20", { fixture: "project/getProjectsPageTwo" })
            .as("pageTwo");
        cy.getBySel("page-btn-2").click();
        cy.wait("@pageTwo");

        cy.contains("If you're reading this its two late").should("be.visible");
        cy.contains("Example project we have mocked").should("not.exist");
    });

    it("scrolls to the final project list item", () => {
        cy.intercept("GET", "/projects?pageNumber=1&pageSize=20", { fixture: "project/getProjects" })
            .as("pageOne");
        cy.visit("/projects");
        cy.wait("@pageOne");

        cy.getBySel("project-list-item-20").scrollIntoView().should("be.visible");
    });

    it("displays only the search results when a search term is entered", () => {
        cy.intercept("GET", "/projects?pageNumber=1&pageSize=20", { fixture: "project/getProjects" })
            .as("getProjects");
        cy.visit("/projects");
        cy.wait("@getProjects");

        cy.intercept("GET", "/projects?pageNumber=1&pageSize=20&search=Example", { fixture: "project/getProjectsSearch" })
            .as("getProjectsSearch");
        cy.getBySel("project-search").type("Example");
        cy.wait("@getProjectsSearch");

        cy.contains("Another project we have mocked").should("not.exist");
        cy.contains("Example project we have mocked").should("be.visible");
    });

    it("displays all projects after a search term has been removed", () => {
        cy.intercept("GET", "/projects?pageNumber=1&pageSize=20", { fixture: "project/getProjects" })
            .as("getProjects");
        cy.visit("/projects");
        cy.wait("@getProjects");

        cy.intercept("GET", "/projects?pageNumber=1&pageSize=20&search=Example", { fixture: "project/getProjectsSearch" })
            .as("getProjectsSearch");
        cy.getBySel("project-search").type("Example");
        cy.wait("@getProjectsSearch");
        cy.getBySel("project-search").clear();

        // assert project which only exists in original getProjects call + does not match search input
        cy.contains("Another project we have mocked").should("be.visible");
    });

    it.skip("displays only the projects that the user has created when they apply the filter", () => {
        cy.intercept("GET", "/projects?pageNumber=1&pageSize=20", { fixture: "project/getProjects" })
            .as("getProjects");
        cy.visit("/projects");
        cy.wait("@getProjects");

        cy.intercept("GET", "/projects?pageNumber=1&pageSize=20&owner=e7d81ffa-aacd-4548-8622-2da50b2fd3e1",
            { fixture: "project/getUserProjects" }
        )
            .as("getUserProjects");
        cy.getBySel("filter-input").first().check();
        cy.wait("@getUserProjects");

        cy.getBySel("project-name").should("contain", "Another project");
        // assert one of the projects that the user did not create is not visible
        cy.contains("Example project").should("not.exist");
    });

    it.skip("displays all projects after the filter has been removed", () => {
        cy.intercept("GET", "/projects?pageNumber=1&pageSize=20", { fixture: "project/getProjects" })
            .as("getProjects");
        cy.visit("/projects");
        cy.wait("@getProjects");

        cy.intercept("GET", "/projects?pageNumber=1&pageSize=20&owner=e7d81ffa-aacd-4548-8622-2da50b2fd3e1",
            { fixture: "project/getUserProjects" }
        )
            .as("getUserProjects");
        cy.getBySel("filter-input").first().check();
        cy.wait("@getUserProjects");

        cy.getBySel("project-name").should("not.contain", "Example project");
    });
});
