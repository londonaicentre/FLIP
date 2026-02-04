/*
 * Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

    interface Chainable {
        /**
         * Get DOM element by data-test attribute.
         *
         * @param {string} selector - The data-test attribute of the target DOM element.
         * @return {HTMLElement} - Target DOM element
         */
        getBySel(value: string): Chainable<Subject>,
        /**
         * Login in to AWS Cognito via Amplify Auth API bypassing UI.
         * @param {string=} [username] - [optional] - The username of the account to login with.
         * @param {string=} [password] - [optional] - The password of the account to login with.
         */
        login(username: string = "", password: string = ""): Chainable<Subject>
    }
}
