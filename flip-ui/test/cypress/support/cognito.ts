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



Cypress.Commands.add("login", (username = "HasAdminRole@gmail.com", password = "NewPassword!1") => {

    const clientId = Cypress.env("VITE_AWS_CLIENT_ID");

    cy.fixture("auth/cognitoAuth")
        .then((cognitoResponse) => {
            window.localStorage.setItem(
                `CognitoIdentityServiceProvider.${clientId}.${username}.idToken`,
                cognitoResponse.AuthenticationResult.IdToken
            );
            window.localStorage.setItem(
                `CognitoIdentityServiceProvider.${clientId}.${username}.accessToken`,
                cognitoResponse.AuthenticationResult.AccessToken
            );
            window.localStorage.setItem(
                `CognitoIdentityServiceProvider.${clientId}.${username}.refreshToken`,
                cognitoResponse.AuthenticationResult.RefreshToken
            );
            window.localStorage.setItem(
                `CognitoIdentityServiceProvider.${clientId}.${username}.clockDrift`,
                "1"
            );
            window.localStorage.setItem(
                `CognitoIdentityServiceProvider.${clientId}.LastAuthUser`,
                username
            );
        });

    cy.saveLocalStorage();
});
