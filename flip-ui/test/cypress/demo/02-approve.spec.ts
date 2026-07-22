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

// Demo segment 2 — the FLIP administrator checks Connection Status, then
// approves the project the researcher staged in segment 1. Approval kicks
// off the imaging import at each trust, which the orchestrator waits out
// off-camera.

import { requireEnv } from "./support/demoFlow";

describe("FLIP demo — administrator approval", () => {
    it("reviews connection status and approves the staged project", () => {
        const email = requireEnv("DEMO_ADMIN_EMAIL");
        const password = requireEnv("DEMO_ADMIN_PASSWORD");
        const projectId = requireEnv("DEMO_PROJECT_ID");

        cy.demoLogin(email, password);
        cy.demoCaption("A FLIP administrator signs in to review the request", 1400);

        cy.visit("/connectionstatus");
        cy.demoCaption("Connection Status — trust connectivity and FL network health at a glance", 600);
        cy.getBySel("trust-row", { timeout: 60000 }).should("have.length.at.least", 1);
        cy.demoPause(2200);
        cy.getBySel("view-toggle-radial").demoClick();
        cy.getBySel("connection-radial-svg", { timeout: 30000 }).should("be.visible");
        cy.demoPause(2600);

        cy.demoCaption("Opening the staged project for review", 600);
        cy.visit(`/project/${projectId}`);
        cy.demoPause(2000);

        // The researcher staged the project in segment 1 — the admin picks
        // the trusts to authorise and approves.
        cy.demoCaption("Approval authorises the imaging import at every selected trust", 800);
        cy.get("[data-test^=\"trust-staged-\"]", { timeout: 90000 })
            .should("have.length.at.least", 1)
            .each(($el) => {
                cy.wrap($el).scrollIntoView();
                cy.wrap($el).demoClick();
            });
        cy.getBySel("approve-project-btn").demoClick();

        cy.contains("Project Approved", { timeout: 90000 }).should("be.visible");
        cy.demoPause(3000);
    });
});
