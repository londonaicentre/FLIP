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

// Records docs/source/assets/admin/assign-trust-admin.gif.

describe("docs: assign a Trust Admin", () => {
    it("makes a researcher the Trust Admin of one trust", () => {
        cy.login();
        cy.intercept("GET", "/users/**/permissions", { fixture: "user/getPermissions" });
        cy.intercept("GET", "/users?pageNumber=1&pageSize=20", { fixture: "user/getUsers" });
        cy.intercept("GET", "/roles", { fixture: "user/getRolesWithTrustAdmin" });
        cy.intercept("POST", "/users/**/roles", { statusCode: 200 }).as("postRoles");

        cy.visit("/admin/users");
        cy.demoPause();

        cy.getBySel("user").contains("Researcher User").demoClick();
        cy.demoPause();

        cy.getBySel("select-trust-admin-role").demoClick();
        cy.demoPause();

        // The "Administers" dropdown inside the card: the trust this Trust Admin decides for.
        cy.getBySel("trust-admin-trust-select").select("UCLH");
        cy.demoPause();

        // The page reloads the list after saving; answer it with the user now a Trust Admin of UCLH, so the
        // recording ends on the saved state rather than the fixture's original role.
        cy.fixture("user/getUsers").then((page) => {
            const saved = structuredClone(page);
            const user = saved.data.find((u: { name: string }) => u.name === "Researcher User");
            user.roles = [{
                id: "8a3d6f14-9b52-4e07-a6c8-1d4f7b2e9053",
                rolename: "Trust Admin",
                roledescription: "Researcher access, plus approving or declining projects for one trust."
            }];
            user.trustAdminOf = {
                id: "53ca8126-5551-41a8-bd0a-587956c859d5",
                code: "UCLH",
                name: "UCLH"
            };
            cy.intercept("GET", "/users?pageNumber=1&pageSize=20", saved);
        });

        cy.getBySel("save-user-btn").demoClick();
        cy.wait("@postRoles").its("request.body.trustId").should("eq", "53ca8126-5551-41a8-bd0a-587956c859d5");
        cy.contains("The user has been updated").should("be.visible");
        cy.demoPause(1200);
    });
});
