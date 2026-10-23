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

// Records docs/source/assets/generated/gifs/flip/trust-auto-upgrade.gif and takes the card screenshots in
// test/cypress/screenshots/ that docs/source/sys-admin/admin-trust-auto-upgrade.rst shows (copied to
// docs/source/assets/flip/trust-auto-upgrade/). The hub answers from a mutable object, so the card walks through
// the states a Trust Admin sees: waiting behind a running job, switched off, outside the window, upgrading,
// failed, up to date.

describe("docs: automatic upgrades on My Trust", () => {
    it("walks the card through its states", () => {
        cy.login({ permissionsFixture: "user/getPermissionsTrustAdmin" });
        cy.intercept("GET", "/users/**/permissions", { fixture: "user/getPermissionsTrustAdmin" });

        const now = () => new Date().toISOString();
        cy.intercept("GET", "**/trust", (req) => {
            req.reply([
                {
                    id: "53ca8126-5551-41a8-bd0a-587956c859d5",
                    name: "UCLH",
                    code: "UCLH",
                    region: "London",
                    last_heartbeat: now(),
                    project_count: 2,
                    services: {},
                    services_updated_at: now()
                }
            ]);
        });
        cy.intercept("GET", "**/trust/*/decisions", { fixture: "trust/decisions" });

        let policy: Record<string, unknown> = {
            enabled: true,
            targetRelease: "v1.3.0",
            currentRelease: "v1.2.0",
            windowStart: "01:00",
            windowEnd: "05:00",
            timezone: "Europe/London",
            state: "PENDING",
            blockers: ["JOBS_RUNNING"],
            pendingSince: "2026-10-20T09:00:00.000Z",
            lastAttempt: null
        };
        cy.intercept("GET", "**/trust/*/auto-upgrade", (req) => req.reply(policy)).as("getPolicy");
        cy.intercept("PUT", "**/trust/*/auto-upgrade", (req) => {
            policy = {
                ...policy,
                ...req.body
            };
            req.reply(policy);
        }).as("putPolicy");

        const shot = (name: string) => {
            // The page has just been re-visited: wait for the card to be laid out at full height first.
            cy.getBySel("auto-upgrade-card").scrollIntoView().should(($el) => {
                expect($el.height()).to.be.greaterThan(400);
            });
            cy.wait(600);
            cy.getBySel("auto-upgrade-card").screenshot(`trust-auto-upgrade-${name}`, { overwrite: true });
        };
        const show = (next: Record<string, unknown>) => {
            // cy.then, not a bare assignment: Cypress queues commands, so this must run in order with them.
            cy.then(() => {
                policy = {
                    ...policy,
                    ...next
                };
            });
            cy.visit("/my-trust");
            cy.wait("@getPolicy");
        };

        cy.visit("/my-trust");
        cy.wait("@getPolicy");
        cy.getBySel("auto-upgrade-card").scrollIntoView();
        cy.demoPause();
        shot("waiting-for-job");

        // Switch off: the card says the Trust is behind and nothing will move it.
        cy.getBySel("auto-upgrade-switch").demoClick();
        cy.wait("@putPolicy");
        cy.getBySel("auto-upgrade-status").should("have.attr", "data-state", "behind");
        cy.demoPause();
        shot("switched-off");

        // Switch back on.
        cy.getBySel("auto-upgrade-switch").demoClick();
        cy.wait("@putPolicy");
        cy.demoPause();

        show({
            enabled: true,
            blockers: ["OUTSIDE_WINDOW"]
        });
        shot("outside-window");

        show({
            state: "IN_PROGRESS",
            blockers: []
        });
        shot("upgrading");

        show({
            state: "FAILED",
            blockers: [],
            lastAttempt: {
                state: "FAILED",
                release: "v1.3.0",
                at: "2026-10-21T02:10:00.000Z",
                detail: "xnat-web did not become healthy within 30m\nImages pulled; nothing was rolled back."
            }
        });
        shot("failed");

        show({
            state: "SUCCEEDED",
            currentRelease: "v1.3.0",
            lastAttempt: {
                state: "SUCCEEDED",
                release: "v1.3.0",
                at: "2026-10-22T02:41:00.000Z",
                detail: null
            }
        });
        shot("up-to-date");
        cy.demoPause(1200);
    });
});
