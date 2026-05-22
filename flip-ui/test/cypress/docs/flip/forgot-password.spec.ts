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

// Records docs/source/assets/flip/forgot-password.gif.
// Mirrors the happy path in test/cypress/integration/group-3/auth/reset_password.spec.ts.

describe("docs: forgot password", () => {
    it("resets a forgotten password", () => {
        const cognitoUrl = "https://cognito-idp.eu-west-2.amazonaws.com/";
        const email = "throwaway.user@kcl.ac.uk";

        cy.visit("/auth/change-password");
        cy.demoPause();

        cy.getBySel("email").demoType(email);
        cy.demoPause();

        cy.intercept("POST", cognitoUrl, {
            statusCode: 200,
            fixture: "auth/cognitoForgotPassword"
        }).as("requestCode");
        cy.getBySel("requestCode-btn").demoClick();
        cy.wait("@requestCode");
        cy.demoPause();

        // The change-password form is a separate <Form> with its own (empty)
        // email field — re-enter it, mirroring reset_password.spec.ts.
        cy.getBySel("email").demoType(email);
        cy.demoPause();

        cy.getBySel("confirmation-code").demoType("12345");
        cy.demoPause();

        cy.getBySel("password").demoType("NewPassword!1");
        cy.demoPause();

        cy.intercept("POST", cognitoUrl, {
            statusCode: 200,
            fixture: "auth/cognitoConfirmForgotPassword"
        }).as("changePassword");
        cy.getBySel("changePassword-btn").demoClick();
        cy.wait("@changePassword");

        cy.contains("You've updated your password. You can now log in using it.").should("be.visible");
        cy.demoPause(1200);
    });
});
