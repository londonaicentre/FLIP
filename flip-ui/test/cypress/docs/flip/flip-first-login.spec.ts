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

// Records docs/source/assets/flip/flip-first-login.gif.
// Mirrors the happy path in test/cypress/integration/group-3/auth/change_temporary_password.spec.ts.

describe("docs: flip first login", () => {
    it("sets a new password on first login", () => {
        cy.intercept("POST", "https://cognito-idp.eu-west-2.amazonaws.com/", {
            fixture: "auth/cognitoNewPasswordRequired"
        });

        cy.visit("/auth/login");
        cy.demoPause();

        cy.getBySel("username").demoType("HasResearcherRole@gmail.com");
        cy.demoPause();

        cy.getBySel("password").demoType("NewPassword!1");
        cy.demoPause();

        cy.getBySel("login-btn").demoClick();
        cy.demoPause();

        cy.getBySel("new-password").demoType("NewPassword!2");
        cy.demoPause();

        cy.getBySel("confirm-new-password").demoType("NewPassword!2");
        cy.demoPause();

        cy.intercept("POST", "https://cognito-idp.eu-west-2.amazonaws.com/", {
            fixture: "auth/cognitoNewPasswordChanged"
        }).as("newPassword");

        cy.getBySel("change-password-btn").demoClick();
        cy.wait("@newPassword");

        cy.contains("Your password has been changed").should("be.visible");
        cy.demoPause(1200);
    });
});
