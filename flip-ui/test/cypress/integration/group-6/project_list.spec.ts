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
        // The status indicator renders human-friendly labels (Staged / Approved / Draft),
        // mapped from the raw STAGED / APPROVED / UNSTAGED enum (see projects.vue STATUS_LABEL).
        cy.getBySel("project-list-item-1").getBySel("project-status-indicator").should("contain", "Staged");
        cy.getBySel("project-list-item-2").getBySel("project-status-indicator").should("contain", "Approved");
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

            // The list redesign turned each row into a router-link itself —
            // there's no separate "View Project" button anymore. Click the
            // row to navigate (project-list-item-0 is the first row).
            cy.getBySel("project-list-item-0").click();

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

    // The layout facts this guards — column x-offsets, rendered font weight, the dot's
    // actual hue — are all computed style. jsdom compiles no Tailwind, so a unit test
    // can only assert the classes; this is the only layer that sees the real thing.
    it("aligns the description column, renders it at body weight, and matches the staged dot to the row spine", () => {
        const trust = (id: string, code: string, approved: boolean) => ({
            id,
            name: `${code} NHS Foundation Trust`,
            code,
            approved
        });
        // Rows deliberately vary in exactly what used to size the `auto` tracks:
        // trust count, next-action text and cohort figure. Newest first, so the
        // STAGED row is row 0 under the default "created" sort.
        const rows = [
            {
                id: "11111111-1111-1111-1111-111111111111",
                name: "Stroke triage",
                description: "Federated stroke triage across participating trusts, with a rather longer blurb.",
                ownerId: "u1",
                ownerEmail: "r.patel@example.com",
                ownerName: "Riya Patel",
                creationtimestamp: "2026-08-03T10:00:00",
                status: "STAGED",
                users: [],
                userCount: 3,
                approvedTrusts: [trust("t1", "GSTT", false), trust("t2", "KCH", true)],
                query: { totalCohort: 1234 }
            },
            {
                id: "22222222-2222-2222-2222-222222222222",
                name: "Chest X-ray screening",
                description: "Short blurb.",
                ownerId: "u1",
                ownerEmail: "r.patel@example.com",
                ownerName: "Riya Patel",
                creationtimestamp: "2026-08-02T10:00:00",
                status: "APPROVED",
                users: [],
                userCount: 9,
                approvedTrusts: [trust("t1", "GSTT", true), trust("t2", "KCH", true),
                    trust("t3", "UCLH", true), trust("t4", "OUH", false)],
                query: { totalCohort: 98765 }
            },
            {
                id: "33333333-3333-3333-3333-333333333333",
                name: "Spleen segmentation",
                description: "Federated spleen segmentation across participating trusts, with a longer blurb again.",
                ownerId: "u1",
                ownerEmail: "r.patel@example.com",
                ownerName: "Riya Patel",
                creationtimestamp: "2026-08-01T10:00:00",
                status: "UNSTAGED",
                users: [],
                userCount: 1,
                approvedTrusts: []
            }
        ];

        cy.viewport(1366, 900);
        cy.intercept("GET", "/projects?pageNumber=1&pageSize=20", {
            body: {
                data: rows,
                totalPages: 1,
                page: 1,
                totalRecords: rows.length
            }
        }).as("getVariedProjects");
        cy.visit("/projects");
        cy.wait("@getVariedProjects");

        // Every row's description starts on the same edge, whatever the other columns hold.
        const lefts: number[] = [];
        cy.getBySel("project-row-description")
            .should("have.length", rows.length)
            .each(($cell) => {
                lefts.push(Math.round($cell[0].getBoundingClientRect().left));
            })
            .then(() => {
                expect(new Set(lefts).size, `description left edges: ${lefts.join(", ")}`).to.equal(1);
            });

        // Body weight: the row is an <a>, which main.css would otherwise render at 600.
        cy.getBySel("project-row-description").first().should("have.css", "font-weight", "400");

        // Staged row: every chip dot is painted the same colour as that row's spine.
        // Asserted hue-agnostically against the spine, so a repaint of the STAGED
        // colour can never leave the dot behind.
        let stagedDotColour = "";
        cy.getBySel("project-list-item-0").within(() => {
            cy.getBySel("project-status-spine").then(($spine) => {
                const spineColour = window.getComputedStyle($spine[0]).backgroundColor;
                cy.getBySel("trust-status-dot")
                    .should("have.length", 2)
                    .each(($dot) => {
                        expect(window.getComputedStyle($dot[0]).backgroundColor).to.equal(spineColour);
                    })
                    .then(($dots) => {
                        stagedDotColour = window.getComputedStyle($dots[0]).backgroundColor;
                    });
            });
        });

        // Approved row: the three signed-off trusts share one colour, distinct from the
        // staged amber, and the trust still pending keeps exactly that amber. Note the
        // dot does NOT match this row's spine — the APPROVED spine is a darker emerald,
        // deliberately, since only the staged colour is shared with the spine.
        cy.getBySel("project-list-item-1").within(() => {
            cy.getBySel("trust-status-dot").should("have.length", 4).then(($dots) => {
                // Chips sort by trust name: GSTT, KCH, OUH (pending), UCLH.
                const colours = [...$dots].map(($dot) => window.getComputedStyle($dot).backgroundColor);
                expect(colours[2], "the pending trust keeps the staged colour").to.equal(stagedDotColour);
                const signedOff = [colours[0], colours[1], colours[3]];
                expect(new Set(signedOff).size, "signed-off trusts share one colour").to.equal(1);
                expect(signedOff[0], "signed-off reads differently from pending").to.not.equal(stagedDotColour);
            });
        });

        // Draft row: chips (none here) and no dots at all.
        cy.getBySel("project-list-item-2").within(() => {
            cy.getBySel("trust-status-dot").should("not.exist");
        });
    });
});
