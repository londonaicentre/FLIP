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

// Records docs/source/assets/admin/create-user.gif.
// Mirrors the happy path in test/cypress/integration/group-4/user/manage_users.spec.ts
// ("Should allow you to register a user") with deliberate pauses.

describe("docs: create user", () => {
    it("registers a new researcher", () => {
        cy.login();
        cy.intercept("GET", "/users/**/permissions", { fixture: "user/getPermissions" });
        cy.intercept("GET", "/users?pageNumber=1&pageSize=20", { fixture: "user/getUsers" });
        cy.intercept("GET", "/roles", { fixture: "user/getRoles" });
        cy.intercept("POST", "step/users", { fixture: "user/postRegisterUser" }).as("registerUser");

        cy.visit("/admin/users");
        cy.demoPause();

        cy.getBySel("register-user-btn").demoClick();
        cy.demoPause();

        cy.getBySel("email-field").demoType("new.researcher@kcl.ac.uk");
        cy.demoPause();

        cy.getBySel("chip-select").demoClick();
        cy.getBySel("chip-select-option").contains("Researcher").demoClick();
        cy.demoPause();

        cy.getBySel("register-user-confirm-btn").demoClick();
        cy.wait("@registerUser");
        cy.contains("The user has been registered successfully").should("be.visible");
        cy.demoPause(1200);
    });
});
