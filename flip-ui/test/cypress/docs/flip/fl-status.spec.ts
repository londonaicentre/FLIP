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

// Records docs/source/assets/flip/fl-status.gif.
// Shows the Connection Status page (/connectionstatus): per-trust online state +
// heartbeats (list and radial federation-topology views) and the FL nets card,
// which is the FL client<->server connectivity detail. There is no functional
// happy-path spec to mirror — /connectionstatus is otherwise exercised only by
// integration/group-3/auth/session_expired.spec.ts — so the mocks are built here.

describe("docs: connection status", () => {
    it("views the federation connection status", () => {
        cy.login();

        // The page derives online/degraded/offline from heartbeat age (<30s =
        // online). A static timestamp would age into "Offline" by the time the
        // GIF is recorded, so reply dynamically with a fresh heartbeat on every
        // poll (SWRV refetches /trust every 15s) to keep both trusts "Online".
        cy.intercept("GET", "**/trust", (req) => {
            const now = new Date().toISOString();
            req.reply([
                {
                    id: "53ca8126-5551-41a8-bd0a-587956c859d5",
                    name: "UCLH",
                    code: "UCLH",
                    region: "London",
                    last_heartbeat: now,
                    project_count: 3
                },
                {
                    id: "4c9692ac-f607-4216-9f0b-b45eb72d83d2",
                    name: "KCH",
                    code: "KCH",
                    region: "London",
                    last_heartbeat: now,
                    project_count: 2
                }
            ]);
        }).as("getTrusts");

        // FL nets card: one healthy net whose two clients are both online, so the
        // card renders "The FL nets are Healthy" with green client check marks.
        cy.intercept("GET", "**/fl/status", {
            statusCode: 200,
            body: [
                {
                    name: "net-1",
                    fl_backend: "nvflare",
                    clients: [
                        {
                            name: "UCLH",
                            code: "UCLH",
                            online: true,
                            status: "online",
                            fl_kit_slot: "Trust_1"
                        },
                        {
                            name: "KCH",
                            code: "KCH",
                            online: true,
                            status: "online",
                            fl_kit_slot: "Trust_2"
                        }
                    ]
                }
            ]
        }).as("getFlStatus");

        cy.visit("/connectionstatus");
        cy.wait("@getTrusts");
        cy.demoPause();

        // List view: trust rows with their "Online" heartbeat state.
        cy.contains("h1", "Connection").should("be.visible");
        cy.getBySel("trust-row").first().contains("Online").should("be.visible");
        cy.demoPause();

        // Radial view: the hub<->trust federation topology.
        cy.getBySel("view-toggle-radial").demoClick();
        cy.demoPause(1000);

        // Back to the list, then reveal the FL nets card — the FL client<->server
        // connectivity detail the page exists to surface.
        cy.getBySel("view-toggle-list").demoClick();
        cy.demoPause();

        cy.contains("The FL nets are").scrollIntoView();
        cy.contains("Healthy").should("be.visible");
        cy.demoPause(1200);
    });
});
