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




const researcherRoleId = "da2d1b85-6bdd-4089-b2fa-3594cc233327";
const adminRoleId = "8ce0351a-33cc-432b-8d6e-161122c712dd";

describe("Manage Users as Researcher", () => {
    it("Should not allow you on the users page without the CanManageUsers Permission", () => {
        cy.login({ permissionsFixture: "user/getPermissionsResearcher" });
        cy.intercept("GET", "/users/**/permissions", { fixture: "user/getPermissionsResearcher" })
            .as("getPermissionsResearcher");
        cy.visit("/admin/users");
        cy.url().should("include", "projects");
    });
});

describe("Manage Users as Administrator", () => {
    beforeEach(() => {
        cy.login();
        cy.intercept("GET", "/users/**/permissions", { fixture: "user/getPermissions" })
            .as("getPermissions");
        cy.intercept("GET", "/users?pageNumber=1&pageSize=20", { fixture: "user/getUsers" })
            .as("getUsers");
        cy.intercept("GET", "/roles", { fixture: "user/getRoles" })
            .as("getRoles");
        cy.visit("/admin/users");
    });

    it("Should allow you to change a user's role", () => {
        cy.getBySel("user").contains("Researcher User").click();
        cy.getBySel("select-admin-role").find("input").check();
        cy.intercept("POST", "/users/**/roles", { statusCode: 200 }).as("postRoles");
        cy.getBySel("save-user-btn").click();
        cy.wait("@postRoles").its("request.body").should("deep.equal",
            { roles: [adminRoleId] });
    });

    it("Should save one selected role for users with existing multiple roles", () => {
        cy.getBySel("user").contains("Multiple Role User").click();
        cy.intercept("POST", "/users/**/roles", { statusCode: 200 }).as("postRoles");
        cy.getBySel("select-researcher-role").find("input").check();
        cy.getBySel("save-user-btn").click();
        cy.wait("@postRoles").its("request.body").should("deep.equal",
            { roles: [researcherRoleId] });
    });

    it("Should show roles as a single selected radio choice", () => {
        cy.getBySel("user").contains("Researcher User").click();
        cy.getBySel("select-researcher-role").find("input").should("be.checked");
        cy.getBySel("select-admin-role").find("input").should("not.be.checked");
    });

    it("Should allow you to register a user", () => {
        cy.getBySel("register-user-btn").click();
        cy.getBySel("name-field").type("Test Person");
        cy.getBySel("organisation-field").type("King's College London");
        cy.getBySel("email-field").type("test.person@kcl.ac.uk");
        cy.getBySel("role-select").click();
        cy.getBySel("role-select-option").contains("Researcher").click();
        cy.intercept("POST", "step/users", { fixture: "user/postRegisterUser" }).as("registerUser");
        cy.getBySel("register-user-confirm-btn").click();

        cy.wait("@registerUser")
            .its("request.body")
            .should("deep.equal", {
                "email": "test.person@kcl.ac.uk",
                "name": "Test Person",
                "organisation": "King's College London",
                "roles": [
                    "da2d1b85-6bdd-4089-b2fa-3594cc233327"
                ]
            });
        cy.contains("The user has been registered successfully").should("be.visible");
    });

    it("handles error on attempt to register a user", () => {
        cy.getBySel("register-user-btn").click();
        cy.getBySel("name-field").type("Test Person");
        cy.getBySel("organisation-field").type("King's College London");
        cy.getBySel("email-field").type("test.person@kcl.ac.uk");
        cy.getBySel("role-select").click();
        cy.getBySel("role-select-option").contains("Researcher").click();
        // step function returns 200 with empty response body if user registration fails
        cy.intercept("POST", "step/users", {}).as("registerUser");
        cy.getBySel("register-user-confirm-btn").click();

        cy.contains("User not registered").should("be.visible");
        cy.contains("There was an error, please try again.").should("be.visible");
    });

    it("Should not allow you to register a user with invalid email", () => {
        cy.getBySel("register-user-btn").click();
        cy.getBySel("role-select").click();
        cy.getBySel("role-select-option").contains("Researcher").click();
        cy.getBySel("name-field").type("Test Person");
        cy.getBySel("organisation-field").type("King's College London");
        cy.getBySel("email-field").type("testperson");
        cy.getBySel("register-user-confirm-btn").click();
        // RegisterUserModal lost the legacy .error_message CSS hook with the
        // Listbox-based role chooser refactor — validation errors now render
        // as plain <div class="text-sm text-red-500"> next to the field. Match
        // on the message text instead, which is the actual contract.
        cy.contains("Please enter a valid email address").should("be.visible");
    });

    it("Should not allow you to register a user without a role", () => {
        cy.getBySel("register-user-btn").click();
        cy.getBySel("name-field").type("Test Person");
        cy.getBySel("organisation-field").type("King's College London");
        cy.getBySel("email-field").type("testperson@test.com");
        cy.getBySel("register-user-confirm-btn").click();
        // Same as the email-validation case — the new modal's role chooser
        // surfaces its yup error inline. Also note the wording changed with
        // the Listbox refactor: "Please select a role" (was "Select at least
        // 1 role" in the old chip-select form).
        cy.contains("Please select a role").should("be.visible");
    });

    it("Should allow you to disable access for a user", () => {
        cy.getBySel("user").contains("Researcher User").click();
        cy.intercept("PUT", "/users/**", { statusCode: 200 }).as("disableUser");
        cy.getBySel("disable-user-btn").click();
        cy.getBySel("confirm-modal-btn").click();
        cy.wait("@disableUser");
        cy.get("@disableUser").its("request.body").should("deep.equal", { disabled: true });
        cy.contains("The user has been disabled.").should("be.visible");
    });

    it("Should allow you to enable access for a disabled user", () => {
        cy.getBySel("user").contains("Disabled User").click();
        cy.intercept("PUT", "/users/**", { statusCode: 200 }).as("enableUser");
        cy.getBySel("enable-user-btn").click();
        cy.getBySel("confirm-modal-btn").click();
        cy.wait("@enableUser");
        cy.get("@enableUser").its("request.body").should("deep.equal", { disabled: false });
        cy.contains("The user has been enabled.").should("be.visible");
    });

    it("allows reset of a user's password", () => {
        cy.getBySel("user").contains("Researcher User").click();
        cy.intercept("POST", "https://cognito-idp.eu-west-2.amazonaws.com/", { statusCode: 200, body: {} })
            .as("passwordReset");
        cy.getBySel("reset-password-btn").click();
        cy.getBySel("confirm-modal-btn").click();
        cy.wait("@passwordReset");
        cy.contains("The user's password has been reset").should("be.visible");
    });

    it("Should support pagination", () => {
        cy.intercept("GET", "/users?pageNumber=2&pageSize=20", { fixture: "user/getUsersPageTwo" })
            .as("pageTwo");
        cy.getBySel("page-btn-2").click();
        cy.wait("@pageTwo");
        cy.contains("Test User").should("be.visible");
    });
});
