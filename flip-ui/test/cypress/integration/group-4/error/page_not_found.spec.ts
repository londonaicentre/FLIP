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




const PageNotFound = {
    Title: "[data-test=title]",
    SubTitle: "[data-test=subtitle]",
    ReturnHomeButton: "[data-test=home-button]"
};

describe("404 Page", () => {
    before(() => {
        cy.login();
    });

    beforeEach(() => {
        cy.restoreLocalStorage();
    });

    it("should display when an unknown url is naviagted to", () => {
        cy.visit("/akjskaj");
        cy.get(PageNotFound.Title).should("exist");
        cy.get(PageNotFound.SubTitle).should("exist");
    });

    it("should allow users to navigate home", () => {
        cy.get(PageNotFound.ReturnHomeButton).click();
        cy.url().should("include", "/projects");
    });
});
