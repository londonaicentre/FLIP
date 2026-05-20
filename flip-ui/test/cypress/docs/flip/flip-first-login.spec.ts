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
// Drives the temporary-password-change flow: Cognito returns NEW_PASSWORD_REQUIRED
// (see auth/cognitoNewPasswordRequired fixture), the user types the temp
// credentials, then sets a permanent password.

const cognitoUrl = "https://cognito-idp.eu-west-2.amazonaws.com/";

describe("docs: first login (set permanent password)", () => {
    it("changes the temporary password on first login", () => {
        cy.intercept("POST", cognitoUrl, { fixture: "auth/cognitoNewPasswordRequired" });
        cy.visit("/auth/login");
        cy.demoPause();

        cy.getBySel("username").demoType("new.researcher@kcl.ac.uk");
        cy.demoPause(300);

        cy.getBySel("password").demoType("TemporaryPass!1");
        cy.demoPause(300);

        cy.getBySel("login-btn").demoClick();
        cy.demoPause();

        cy.getBySel("new-password").demoType("MyNewPassword!1");
        cy.demoPause(300);

        cy.getBySel("confirm-new-password").demoType("MyNewPassword!1");
        cy.demoPause(300);

        cy.intercept("POST", cognitoUrl, { fixture: "auth/cognitoNewPasswordChanged" });
        cy.getBySel("change-password-btn").demoClick();
        cy.demoPause(1500);
    });
});
