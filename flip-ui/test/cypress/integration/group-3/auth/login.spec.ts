/*
 * Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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




describe("the login page", () => {
    beforeEach(() => {
        cy.visit("/auth/login");
    });

    it("validates form fields and logs in as expected", () => {
        cy.getBySel("login-btn").click();
        cy.contains("An email address is required").should("be.visible");
        cy.contains("Your password must be at least 8 characters").should("be.visible");
        cy.getBySel("username").clear().type("username@@");
        cy.getBySel("password").clear().type("NewPassword!1");
        cy.getBySel("login-btn").click();
        cy.contains("Please enter a valid email address").should("be.visible");

        cy.log("Shows error with invalid password");
        cy.getBySel("username").clear().type("HasResearcherRole@gmail.com");
        cy.getBySel("password").clear().type("Pass");
        cy.getBySel("login-btn").click();
        cy.contains("Your password must be at least 8 characters").should("be.visible");

        cy.intercept(
            "POST",
            "https://cognito-idp.eu-west-2.amazonaws.com/", { statusCode: 500 }
        ).as("failedLogin");

        cy.log("Shows error with wrong password");
        cy.getBySel("username").clear().type("HasAdminRole@gmail.com");
        cy.getBySel("password").clear().type("Password2!");
        cy.getBySel("login-btn").click();
        cy.contains("There was a problem logging you in. Please check your details and try again.")
            .should("be.visible");

        cy.getBySel("username").clear().type("some-email@address.com");
        cy.getBySel("password").clear().type("Password2!");
        cy.getBySel("login-btn").click();
        cy.contains("There was a problem logging you in. Please check your details and try again.")
            .should("be.visible");

        cy.log("Logins in and redirects");

        cy.intercept(
            "POST",
            "https://cognito-idp.eu-west-2.amazonaws.com/", {
            statusCode: 200,
            fixture: "auth/cognitoAuth"
        }
        ).as("successfulLogin");

        cy.getBySel("username").clear().type("HasAdminRole@gmail.com");
        cy.getBySel("password").clear().type("NewPassword!1");
        cy.getBySel("login-btn").click();
        cy.url().should("include", "/projects");
        cy.getBySel("account-menu-btn").click();
        cy.contains("HasAdminRole@gmail.com").should("be.visible");
    });

    it("navigates to access request page upon clicking 'Request access'", () => {
        cy.getBySel("request-access-btn")
            .should("be.visible")
            .click();

        cy.url().should("include", "/access-request");
    });
});
