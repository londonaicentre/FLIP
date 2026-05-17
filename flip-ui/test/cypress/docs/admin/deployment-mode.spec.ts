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

// Records docs/source/assets/admin/deployment-mode.gif.
// The global /site/details intercept (test/cypress/support/globalIntercepts.ts)
// returns the JSON fixture admin/bannerDisabled with deploymentMode=false, so
// the toggle button reads "Enable Deployment Mode" and opens the confirm modal.

describe("docs: deployment mode", () => {
    it("enables deployment mode", () => {
        cy.login();

        cy.visit("/admin/deployments");
        cy.demoPause(800);

        cy.contains("button", "Enable Deployment Mode").click();
        cy.demoPause();

        cy.getBySel("confirm-modal-btn").click();
        cy.demoPause(1200);
    });
});
