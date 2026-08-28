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




/// <reference types="cypress" />

declare namespace Cypress {

    /**
    * Window type for Application Under Test(AUT)
    */
    type AUTWindow = Window & typeof globalThis & ApplicationWindow

    /**
    * The interface for user-defined properties in Window object under test.
    */
    interface ApplicationWindow {
        pinia: Pinia;
    }

    /**
    * Options accepted by cy.login — the type is owned by the implementation in
    * test/cypress/support/cognito.ts. The inline import() keeps this file a
    * script: a top-level import would turn it into a module and stop this
    * `declare namespace Cypress` from merging into the global one.
    */
    type LoginOptions = import("./cypress/support/cognito").LoginOptions;

    interface Chainable<Subject = any> {
        /**
         * Get DOM element by data-test attribute.
         *
         * Yields what the underlying cy.get yields. Declared as such rather than as
         * Chainable<Subject>: called on `cy` the current subject is `undefined`, so a
         * `.then(($el) => ...)` off the result would type `$el` as `undefined`.
         *
         * @param {string} selector - The data-test attribute of the target DOM element.
         * @return {JQuery<HTMLElement>} - Target DOM element(s)
         */
        getBySel(
            value: string,
            options?: Partial<Loggable & Timeoutable & Withinable & Shadow>
        ): Chainable<JQuery<HTMLElement>>,
        /**
         * Login in to AWS Cognito via Amplify Auth API bypassing UI.
         *
         * Accepts either an options object or, for the legacy call sites that
         * predate it, a bare username string — matching the implementation in
         * test/cypress/support/cognito.ts.
         *
         * @param {LoginOptions|string=} [options] - [optional] - Login options, or a username.
         */
        login(options?: LoginOptions | string): Chainable<Subject>
    }
}
