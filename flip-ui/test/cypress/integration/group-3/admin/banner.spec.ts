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




import { bannerDisabled, bannerEnabled } from "@test/cypress/fixtures/admin/details";

describe("Banner", () => {
    before(() => {
        cy.login();
    });

    beforeEach(() => {
        cy.restoreLocalStorage();
    });

    it("shows the banner enabled", () => {
        cy.intercept("GET",
            "/site/details",
            { body: bannerEnabled }
        ).as("siteDetailsGet");

        cy.intercept("PUT",
            "/site/details",
            { body: bannerEnabled }
        ).as("siteDetailsPut");

        cy.visit("/admin/banner");

        cy.getBySel("site-banner").should("be.visible");
        cy.getBySel("banner-message").should("be.visible").should("have.text", bannerEnabled.banner.message);
        cy.getBySel("banner-link").should("be.visible").should("have.attr", "href", bannerEnabled.banner.link);
    });

    it("does not show the banner when disabled", () => {
        cy.intercept("GET",
            "/site/details",
            { body: bannerDisabled }
        ).as("siteDetailsGet");

        cy.intercept("PUT",
            "/site/details",
            { body: bannerDisabled }
        ).as("siteDetailsPut");

        cy.visit("/admin/banner");

        cy.getBySel("site-banner").should("not.exist");
    });

    it("shows the edit banner details", () => {
        cy.intercept("GET",
            "/site/details",
            { body: bannerDisabled }
        ).as("siteDetailsGet");

        cy.intercept("PUT",
            "/site/details",
            { body: bannerDisabled }
        ).as("siteDetailsPut");

        cy.visit("/admin/banner");

        cy.getBySel("edit-banner-message").should("have.value", bannerDisabled.banner.message);
        cy.getBySel("edit-banner-link").should("have.value", bannerDisabled.banner.link);
    });
});
