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




describe("Change Temporary Password Page", () => {
    beforeEach(() => {
        cy.intercept("POST", "https://cognito-idp.eu-west-2.amazonaws.com/", { fixture: "auth/cognitoNewPasswordRequired" });
        cy.visit("/auth/login");
        cy.getBySel("username").clear().type("HasResearcherRole@gmail.com");
        cy.getBySel("password").clear().type("NewPassword!1");
    });

    it("should be able to change password", () => {
        cy.getBySel("login-btn").click();

        cy.getBySel("new-password").clear().type("Pass13!");

        cy.getBySel("confirm-new-password").clear().type("Pass13!");

        cy.getBySel("change-password-btn").click();

        cy.get(".error_message").eq(0).should("have.text", "Your password must be at least 8 characters");
        cy.get(".error_message").eq(1).should("have.text", "Your password must be at least 8 characters");

        cy.getBySel("new-password").clear().type("Password!");

        cy.getBySel("confirm-new-password").clear().type("Password!");

        cy.getBySel("change-password-btn").click();

        cy.get(".error_message").eq(0).should("have.text", "Your password must contain a number");
        cy.get(".error_message").eq(1).should("have.text", "Your password must contain a number");

        cy.getBySel("new-password").clear().type("password13!");
        cy.getBySel("confirm-new-password").clear().type("password13!");
        cy.getBySel("change-password-btn").click();

        cy.get(".error_message").eq(0)
            .should("have.text", "Your password must contain an uppercase character");
        cy.get(".error_message").eq(1)
            .should("have.text", "Your password must contain an uppercase character");

        cy.getBySel("new-password").clear().type("PASSWORD13!");
        cy.getBySel("confirm-new-password").clear().type("PASSWORD13!");
        cy.getBySel("change-password-btn").click();

        cy.get(".error_message").eq(0)
            .should("have.text", "Your password must contain a lowercase character");
        cy.get(".error_message").eq(1)
            .should("have.text", "Your password must contain a lowercase character");

        cy.getBySel("new-password").clear().type("Pass12345");
        cy.getBySel("confirm-new-password").clear().type("Pass12345");
        cy.getBySel("change-password-btn").click();

        cy.get(".error_message").eq(0).should("have.text", "Your password must contain a special character");
        cy.get(".error_message").eq(1).should("have.text", "Your password must contain a special character");

        cy.getBySel("new-password").clear().type("Pass12345!");
        cy.getBySel("confirm-new-password").clear().type("NotPass1234!");
        cy.getBySel("change-password-btn").click();

        cy.get(".error_message").eq(0).should("have.text", "Your passwords do not match");

        cy.getBySel("new-password").clear().type("NewPassword!2");
        cy.getBySel("confirm-new-password").clear().type("NewPassword!2");

        cy.intercept("POST", "https://cognito-idp.eu-west-2.amazonaws.com/", { fixture: "auth/cognitoNewPasswordChanged" }).as("newPassword");

        cy.getBySel("change-password-btn").click();

        cy.wait("@newPassword");

        cy.getBySel("password-changed-message").should("have.text", " Your password has been changed ");

        cy.getBySel("login-btn").click();

        cy.url().should("include", "/login");
    });
});
