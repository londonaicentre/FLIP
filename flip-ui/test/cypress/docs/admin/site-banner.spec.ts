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

// Records docs/source/assets/admin/site-banner.gif.
// The bannerDisabled fixture starts with banner.enabled=false, so the toggle
// button reads "Enable Site Banner" and clicking it opens the confirm modal.

import { bannerDisabled, bannerEnabled } from "@test/cypress/fixtures/admin/details";

describe("docs: site banner", () => {
    it("enables the site banner", () => {
        cy.login();
        cy.intercept("GET", "/site/details", { body: bannerDisabled }).as("siteDetailsGet");
        cy.intercept("PUT", "/site/details", { body: bannerEnabled }).as("siteDetailsPut");

        cy.visit("/admin/banner");
        cy.demoPause(800);

        cy.contains("button", "Enable Site Banner").demoClick();
        cy.demoPause();

        cy.getBySel("confirm-modal-btn").demoClick();
        cy.wait("@siteDetailsPut");
        cy.demoPause(1200);
    });
});
