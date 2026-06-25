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

// Records docs/source/assets/admin/user-enable-disable.gif.
// Shows both halves of the toggle in one clip: disable an active user, then
// re-enable a disabled user.

describe("docs: enable / disable user", () => {
    it("disables then enables a user", () => {
        cy.login();
        cy.intercept("GET", "/users/**/permissions", { fixture: "user/getPermissions" });
        cy.intercept("GET", "/users?pageNumber=1&pageSize=20", { fixture: "user/getUsers" });
        cy.intercept("GET", "/roles", { fixture: "user/getRoles" });
        cy.intercept("PUT", "/users/**", { statusCode: 200 }).as("toggleUser");

        cy.visit("/admin/users");
        cy.demoPause();

        // Disable an active user.
        cy.getBySel("user").contains("researcher.user@flip.com").demoClick();
        cy.demoPause();
        cy.getBySel("more-options-btn").demoClick();
        cy.demoPause();
        cy.getBySel("disable-user-btn").demoClick();
        cy.demoPause();
        cy.getBySel("confirm-modal-btn").demoClick();
        cy.wait("@toggleUser");
        cy.contains("The user has been disabled").should("be.visible");
        cy.demoPause(900);

        // Re-enable the user that's already disabled in the fixture.
        cy.getBySel("user").contains("disabled.user@flip.com").demoClick();
        cy.demoPause();
        cy.getBySel("more-options-btn").demoClick();
        cy.demoPause();
        cy.getBySel("enable-user-btn").demoClick();
        cy.demoPause();
        cy.getBySel("confirm-modal-btn").demoClick();
        cy.wait("@toggleUser");
        cy.contains("The user has been enabled").should("be.visible");
        cy.demoPause(1200);
    });
});
