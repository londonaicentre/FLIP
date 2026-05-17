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
// Mirrors the happy path in integration/group-3/auth/reset_password.spec.ts:
// request a code → enter the code + new password → submit.

const cognitoUrl = "https://cognito-idp.eu-west-2.amazonaws.com/";

describe("docs: forgot password", () => {
    it("requests a reset code and sets a new password", () => {
        cy.visit("/auth/change-password");
        cy.demoPause();

        cy.getBySel("email").demoType("researcher.user@flip.com");
        cy.demoPause(300);

        cy.intercept("POST", cognitoUrl, { statusCode: 200, fixture: "auth/cognitoForgotPassword" }).as("requestCode");
        cy.getBySel("requestCode-btn").demoClick();
        cy.wait("@requestCode");
        cy.demoPause();

        // After requestCode resolves, the code-entry form mounts as a second
        // <Form> with its own email field — it doesn't inherit the value typed
        // above, so re-type all three fields here.
        cy.getBySel("email").demoType("researcher.user@flip.com");
        cy.demoPause(300);

        cy.getBySel("confirmation-code").demoType("123456");
        cy.demoPause(300);

        cy.getBySel("password").demoType("MyNewPassword!1");
        cy.demoPause(300);

        cy.intercept("POST", cognitoUrl, { statusCode: 200, fixture: "auth/cognitoConfirmForgotPassword" }).as("changePassword");
        cy.getBySel("changePassword-btn").demoClick();
        cy.wait("@changePassword");
        cy.demoPause(1500);
    });
});
