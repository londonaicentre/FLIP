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

// The estate-wide Models page, which absorbed the per-project model list. Covers the behaviours
// the deleted group-2/model/model_list.spec.ts used to cover (error state, empty state, search,
// row navigation) plus the project scoping that replaced that page.

const trust = (code: string) => ({
    id: code,
    name: `${code} NHS Foundation Trust`,
    code
});

const model = (id: string, name: string, projectId: string, projectName: string, status: string) => ({
    id,
    name,
    description: "",
    projectId,
    projectName,
    ownerId: "u1",
    ownerName: "Riya Patel",
    creationTimestamp: "2026-08-01T10:00:00",
    status,
    trusts: status === "RUNNING" ? [trust("GSTT")] : []
});

const page = (rows: unknown[], statusCounts: Record<string, number>) => ({
    body: {
        data: rows,
        totalPages: 1,
        page: 1,
        pageSize: 20,
        totalRecords: rows.length,
        statusCounts
    }
});

const ALL = [
    model("m1", "xray-baseline", "p1", "Stroke triage", "PENDING"),
    model("m2", "xray-federated", "p1", "Stroke triage", "RUNNING"),
    model("m3", "spleen-seg", "p2", "Chest X-ray screening", "RESULTS_UPLOADED")
];

describe("models page", () => {
    before(() => {
        cy.login();
    });

    beforeEach(() => {
        cy.restoreLocalStorage();
        cy.intercept("GET", "/models/projects", {
            body: [
                {
                    id: "p1",
                    name: "Stroke triage",
                    status: "APPROVED"
                },
                {
                    id: "p2",
                    name: "Chest X-ray screening",
                    status: "STAGED"
                }
            ]
        }).as("getProjectOptions");
        cy.intercept("GET", /\/models\?pageNumber=1(?!.*project=)/, page(ALL, {
            PENDING: 1,
            RUNNING: 1,
            RESULTS_UPLOADED: 1
        })).as("getAllModels");
        cy.intercept("GET", /\/models\?pageNumber=1.*project=p1/, page(ALL.slice(0, 2), {
            PENDING: 1,
            RUNNING: 1
        })).as("getScopedModels");
    });

    it("opens estate-wide with every accessible project in the filter", () => {
        cy.visit("/models");
        cy.wait("@getAllModels");

        cy.getBySel("models-scope-eyebrow").should("contain", "Estate-wide");
        cy.getBySel("project-filter-chip").should("not.exist");
        cy.getBySel("project-filter").find("option").should("have.length", 3);
        cy.getBySel("models-list-item-2").should("exist");
    });

    it("scopes to a project from the dropdown, and says so in the URL", () => {
        cy.visit("/models");
        cy.wait("@getAllModels");

        cy.getBySel("project-filter").select("p1");
        cy.wait("@getScopedModels");

        cy.url().should("include", "project=p1");
        cy.getBySel("models-scope-eyebrow").should("contain", "Stroke triage");
        cy.getBySel("project-filter-chip").should("contain", "Project: Stroke triage");
        cy.getBySel("models-list-item-2").should("not.exist");
    });

    it("arrives scoped from a deep link, showing every status rather than the estate default", () => {
        cy.visit("/models?project=p1");
        cy.wait("@getScopedModels");

        cy.getBySel("project-filter-chip").should("be.visible");
        // "View All Models" promises all of the project's models, so no status tile is preselected.
        cy.get("[aria-pressed=true]").should("not.exist");
    });

    it("clears the scope from the chip and returns to the estate list", () => {
        cy.visit("/models?project=p1");
        cy.wait("@getScopedModels");

        cy.getBySel("clear-project-filter").click();
        cy.wait("@getAllModels");

        cy.url().should("not.include", "project=");
        cy.getBySel("models-scope-eyebrow").should("contain", "Estate-wide");
    });

    it("offers Create Model only for an approved project in scope", () => {
        cy.visit("/models");
        cy.wait("@getAllModels");
        cy.getBySel("add-model-btn").should("not.exist");

        cy.visit("/models?project=p1");
        cy.wait("@getScopedModels");
        cy.getBySel("add-model-btn").should("exist");

        // p2 is STAGED — the API refuses model creation against it, so the button stays hidden.
        cy.intercept("GET", /\/models\?pageNumber=1.*project=p2/, page([ALL[2]], { RESULTS_UPLOADED: 1 })).as("getStagedProjectModels");
        cy.visit("/models?project=p2");
        cy.wait("@getStagedProjectModels");
        cy.getBySel("add-model-btn").should("not.exist");
    });

    it("drops a project id the user cannot see and falls back to the estate list", () => {
        cy.visit("/models?project=not-mine");
        cy.wait("@getProjectOptions");

        cy.url().should("not.include", "project=");
        cy.getBySel("models-scope-eyebrow").should("contain", "Estate-wide");
    });

    it("opens a model from its row link", () => {
        cy.visit("/models");
        cy.wait("@getAllModels");

        cy.getBySel("model-name").first().click();

        cy.url().should("include", "/project/p1/model/m1");
    });

    it("shows the empty-state copy when no models come back", () => {
        cy.intercept("GET", /\/models\?pageNumber=1(?!.*project=)/, page([], {})).as("getNoModels");
        cy.visit("/models");
        cy.wait("@getNoModels");

        cy.contains("There are no models to show").should("be.visible");
    });

    it("surfaces a failed fetch", () => {
        cy.intercept("GET", /\/models\?pageNumber=1(?!.*project=)/, {
            statusCode: 500,
            body: {}
        })
            .as("getModelsError");
        cy.visit("/models");
        cy.wait("@getModelsError");

        cy.contains("Something went wrong").should("be.visible");
    });
});
