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
        cy.visit("/connectionstatus");

        // Simulate Amplify's Hub firing tokenRefresh_failure — what happens
        // in production when the refresh token is rejected by Cognito.
        // The Hub listener in src/utils/auth.ts redirects to /auth/login
        // and shows the "session expired" snackbar.
        //
        // Cypress mode bypasses Amplify's session/refresh round-trip (see
        // src/utils/auth.ts → window.Cypress branch), so the real refresh
        // call never happens in tests; firing the Hub event directly
        // exercises the listener that owns the user-facing behaviour.
        cy.window().its("__cypressTriggerSessionExpiry").then((trigger) => {
            (trigger as () => void)();
        });

        cy.url({ timeout: 10_000 }).should("not.include", "/connectionstatus");
        cy.contains("Sign in to FLIP").should("be.visible");
        cy.getBySel("snackbar-text")
            .contains("Your session has expired. Please log in again.")
            .should("be.visible");
    });
});
