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
        cy.login();
        cy.intercept("GET", "/users/**/permissions", { fixture: "user/getPermissionsResearcher" })
            .as("getPermissionsResearcher");
        cy.visit("/admin/users");
        cy.wait("@getPermissionsResearcher");
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

    it("Should allow you to assign a role to a user", () => {
        cy.getBySel("user").contains("researcher.user@flip.com").click();
        cy.getBySel("add-admin-btn").click();
        cy.intercept("POST", "/users/**/roles", { statusCode: 200 }).as("postRoles");
        cy.getBySel("save-user-btn").click();
        cy.wait("@postRoles").its("request.body").should("deep.equal",
            { roles: [researcherRoleId, adminRoleId] });
    });

    it("Should allow you to remove a role from a user who has more than one role", () => {
        cy.getBySel("user").contains("multipleRole.user@flip.com").click();
        cy.intercept("POST", "/users/**/roles", { statusCode: 200 }).as("postRoles");
        cy.getBySel("remove-admin-btn").click();
        cy.getBySel("save-user-btn").click();
        cy.wait("@postRoles").its("request.body").should("deep.equal",
            { roles: [researcherRoleId] });
    });

    it("Should not allow you to remove a user's only role", () => {
        cy.getBySel("user").contains("researcher.user@flip.com").click();
        cy.getBySel("remove-researcher-btn").click();
        cy.contains("You need to have at least 1 role assigned to a user.");
    });

    it("Should allow you to register a user", () => {
        cy.getBySel("register-user-btn").click();
        cy.getBySel("email-field").type("test.person@kcl.ac.uk");
        cy.getBySel("chip-select").click();
        cy.getBySel("chip-select-option").contains("Researcher").click();
        cy.intercept("POST", "step/users", { fixture: "user/postRegisterUser" }).as("registerUser");
        cy.getBySel("register-user-confirm-btn").click();

        cy.wait("@registerUser")
            .its("request.body")
            .should("deep.equal", {
                "email": "test.person@kcl.ac.uk",
                "roles": [
                    "da2d1b85-6bdd-4089-b2fa-3594cc233327"
                ]
            });
        cy.contains("The user has been registered successfully").should("be.visible");
    });

    it("handles error on attempt to register a user", () => {
        cy.getBySel("register-user-btn").click();
        cy.getBySel("email-field").type("test.person@kcl.ac.uk");
        cy.getBySel("chip-select").click();
        cy.getBySel("chip-select-option").contains("Researcher").click();
        // step function returns 200 with empty response body if user registration fails
        cy.intercept("POST", "step/users", {}).as("registerUser");
        cy.getBySel("register-user-confirm-btn").click();

        cy.contains("User not registered").should("be.visible");
        cy.contains("There was an error, please try again.").should("be.visible");
    });

    it("Should not allow you to register a user with invalid email", () => {
        cy.getBySel("register-user-btn").click();
        cy.getBySel("chip-select").click();
        cy.getBySel("chip-select-option").contains("Researcher").click();
        cy.getBySel("email-field").type("testperson");
        cy.getBySel("register-user-confirm-btn").click();
        cy.get(".error_message").should("have.text", "Please enter a valid email address");
    });

    it("Should not allow you to register a user without a role", () => {
        cy.getBySel("register-user-btn").click();
        cy.getBySel("email-field").type("testperson@test.com");
        cy.getBySel("register-user-confirm-btn").click();
        cy.get(".error_message").should("have.text", "Select at least 1 role");
    });

    it("Should allow you to disable access for a user", () => {
        cy.getBySel("user").contains("researcher.user@flip.com").click();
        cy.intercept("PUT", "/users/**", { statusCode: 200 }).as("disableUser");
        cy.getBySel("more-options-btn").click();
        cy.getBySel("disable-user-btn").click();
        cy.getBySel("confirm-modal-btn").click();
        cy.wait("@disableUser");
        cy.get("@disableUser").its("request.body").should("deep.equal", { disabled: true });
        cy.contains("The user has been disabled.").should("be.visible");
    });

    it("Should allow you to enable access for a disabled user", () => {
        cy.getBySel("user").contains("disabled.user@flip.com").click();
        cy.intercept("PUT", "/users/**", { statusCode: 200 }).as("enableUser");
        cy.getBySel("more-options-btn").click();
        cy.getBySel("enable-user-btn").click();
        cy.getBySel("confirm-modal-btn").click();
        cy.wait("@enableUser");
        cy.get("@enableUser").its("request.body").should("deep.equal", { disabled: false });
        cy.contains("The user has been enabled.").should("be.visible");
    });

    it("allows reset of a user's password", () => {
        cy.getBySel("user").contains("researcher.user@flip.com").click();
        cy.intercept("POST", "https://cognito-idp.eu-west-2.amazonaws.com/", { statusCode: 200 })
            .as("passwordReset");
        cy.getBySel("more-options-btn").click();
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
        cy.contains("test.user@flip.com").should("be.visible");
    });
});
