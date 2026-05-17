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
// The Filters popover is xl:hidden, so on the 1280-wide docs viewport the
// keyword search input on the project list (`project-search`) is the visible
// filtering surface. Type into it and watch the list narrow.

describe("docs: filter projects", () => {
    it("filters the project list by a keyword", () => {
        cy.login();
        cy.intercept("GET", "/projects?pageNumber=1&pageSize=20", { fixture: "project/getProjects" });
        cy.intercept("GET", /\/projects\?pageNumber=1&pageSize=20.*search=.*/, {
            fixture: "project/getProjectsSearch"
        }).as("searchProjects");

        cy.visit("/projects");
        cy.demoPause();

        cy.getBySel("project-search").demoType("stroke");
        cy.wait("@searchProjects");
        cy.demoPause(1500);
    });
});
