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

// Records docs/source/assets/admin/reset-password.gif.

describe("docs: reset user password", () => {
    it("resets a researcher's password", () => {
        cy.login();
        cy.intercept("GET", "/users/**/permissions", { fixture: "user/getPermissions" });
        cy.intercept("GET", "/users?pageNumber=1&pageSize=20", { fixture: "user/getUsers" });
        cy.intercept("GET", "/roles", { fixture: "user/getRoles" });
        cy.intercept("POST", "https://cognito-idp.eu-west-2.amazonaws.com/", {
            statusCode: 200,
            body: {}
        }).as("passwordReset");

        cy.visit("/admin/users");
        cy.demoPause();

        cy.getBySel("user").contains("researcher.user@flip.com").demoClick();
        cy.demoPause();

        cy.getBySel("more-options-btn").demoClick();
        cy.demoPause();

        cy.getBySel("reset-password-btn").demoClick();
        cy.demoPause();

        cy.getBySel("confirm-modal-btn").demoClick();
        cy.wait("@passwordReset");
        cy.contains("The user's password has been reset").should("be.visible");
        cy.demoPause(1200);
    });
});
