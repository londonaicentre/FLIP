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

// Records docs/source/assets/flip/flip-login.gif.
// Mirrors the happy path in test/cypress/integration/group-3/auth/login.spec.ts.

describe("docs: flip login", () => {
    it("signs in with email and password", () => {
        cy.intercept("POST", "https://cognito-idp.eu-west-2.amazonaws.com/", {
            statusCode: 200,
            fixture: "auth/cognitoAuth"
        }).as("login");

        cy.visit("/auth/login");
        cy.demoPause();

        cy.getBySel("username").demoType("HasAdminRole@gmail.com");
        cy.demoPause();

        cy.getBySel("password").demoType("NewPassword!1");
        cy.demoPause();

        cy.getBySel("login-btn").demoClick();
        cy.wait("@login");

        cy.url().should("include", "/projects");
        cy.demoPause(1200);
    });
});
