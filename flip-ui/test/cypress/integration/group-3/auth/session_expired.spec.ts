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



describe("session expiry tests", () => {
    beforeEach(() => {
        cy.login();
    });

    it("logs the user out upon session expiry", () => {
        cy.intercept(
            "POST",
            "https://cognito-idp.eu-west-2.amazonaws.com/", {
            statusCode: 400,
            fixture: "auth/cognitoNotAuthorizedResponse"
        })
            .as("expiredSession");

        cy.visit("/connectionstatus");
        cy.wait("@expiredSession");
        cy.url().should("not.include", "/connectionstatus");
        cy.contains("Log into your account").should("be.visible");

        cy.getBySel("snackbar")
            .contains("You've been signed out")
            .should("be.visible")
            .its("length")
            .should("eq", 1)
            .getBySel("snackbar-text")
            .contains("Your session has expired. Please log in again.")
            .should("be.visible");
    });
});
