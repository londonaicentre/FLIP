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

// Records docs/source/assets/flip/my-trust-approve.gif.
// A Trust Admin's My Trust page (FLIP#1258): the projects staged at their trust beside the trust's own
// connection card. The trust list is answered live on every poll, as in fl-status.spec.ts, so the card shows the
// trust online rather than ageing into Offline while the GIF records.

describe("docs: approve a project for your trust", () => {
    it("approves a project from the My Trust page", () => {
        cy.login({ permissionsFixture: "user/getPermissionsTrustAdmin" });
        cy.intercept("GET", "/users/**/permissions", { fixture: "user/getPermissionsTrustAdmin" });

        type ServiceProbe = { status: string; version: string | null; response_ms: number | null };
        const services: Record<string, ServiceProbe> = {
            "trust-api": {
                status: "healthy",
                version: "v0.9.0",
                response_ms: null
            },
            xnat: {
                status: "healthy",
                version: "1.10.0",
                response_ms: 18
            },
            "imaging-api": {
                status: "healthy",
                version: "v0.9.0",
                response_ms: 12
            },
            omop: {
                status: "healthy",
                version: null,
                response_ms: 2
            },
            dicom: {
                status: "healthy",
                version: null,
                response_ms: 31
            },
            "data-access-api": {
                status: "healthy",
                version: "v0.9.0",
                response_ms: 15
            }
        };
        cy.intercept("GET", "**/trust", (req) => {
            const now = new Date().toISOString();
            req.reply([
                {
                    id: "53ca8126-5551-41a8-bd0a-587956c859d5",
                    name: "UCLH",
                    code: "UCLH",
                    region: "London",
                    last_heartbeat: now,
                    project_count: 2,
                    services,
                    services_updated_at: now
                }
            ]);
        }).as("getTrusts");
        cy.intercept("GET", "**/trust/*/decisions", { fixture: "trust/decisions" }).as("getDecisions");
        cy.intercept("POST", "/step/project/*/approve", {
            statusCode: 200,
            body: {
                projectStatus: "APPROVED",
                successful: true,
                details: []
            }
        }).as("approve");

        cy.visit("/my-trust");
        cy.wait("@getDecisions");
        cy.demoPause();

        // Each pending request is a card with its facts; open the decided history first, then approve.
        cy.getBySel("decided-list").find("[data-test=decision-row-toggle]").first().demoClick();
        cy.demoPause();

        cy.getBySel("pending-list").find("[data-test=approve-btn]").first().demoClick();
        cy.demoPause();
        cy.getBySel("confirm-modal-btn").demoClick();

        cy.wait("@approve").its("request.body").should("deep.equal", {
            trusts: ["53ca8126-5551-41a8-bd0a-587956c859d5"],
            declined: []
        });
        cy.contains("Project approved").should("be.visible");
        cy.demoPause(1200);
    });
});
