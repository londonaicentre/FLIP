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

// cy.login writes a fully-formed AmplifyUser into the `cypress.auth.user`
// localStorage key. The auth route guard in src/utils/auth.ts checks for this
// when running under Cypress and populates the auth store directly, bypassing
// Amplify v6's `fetchAuthSession()` / `fetchUserAttributes()` round-trip.
//
// Going through the live Amplify flow would require simulating Cognito's SRP
// handshake, which a static fixture can't do — so the production flow uses
// real Cognito and the test flow uses this hook. The auth-related specs in
// group-3 still drive the real `signIn` codepath through the login form.

export interface LoginOptions {
    username?: string;
    permissionsFixture?: string;
}

const DEFAULT_USER_ID = "f10ff491-9418-49fa-9ed7-e0fe4bd01c58";

Cypress.Commands.add("login", (options: LoginOptions | string = {}) => {
    // Support the legacy `cy.login("user@example.com", "password")` shape that
    // some specs still use.
    const opts: LoginOptions = typeof options === "string" ? { username: options } : options;
    const username = opts.username ?? "HasAdminRole@gmail.com";
    const permissionsFixture = opts.permissionsFixture ?? "user/getPermissions";

    cy.fixture(permissionsFixture).then((perms: { permissions: string[] }) => {
        const user = {
            username: DEFAULT_USER_ID,
            userId: DEFAULT_USER_ID,
            attributes: {
                sub: DEFAULT_USER_ID,
                email: username
            },
            permissions: perms.permissions ?? []
        };

        window.localStorage.setItem("cypress.auth.user", JSON.stringify(user));
    });

    cy.saveLocalStorage();
});
