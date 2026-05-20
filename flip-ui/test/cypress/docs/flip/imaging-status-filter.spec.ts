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

// Records docs/source/assets/flip/imaging-status-filter.gif.
// On an approved project, the ProjectStatus panel shows per-trust imaging
// status with an AiSearch filter (data-test=filter-project-status). Open
// the project, scroll the panel into view, type a trust name fragment.

const PROJECT_ID = "6fcbdd40-3675-45c9-899e-1a005e5245ba";

describe("docs: filter imaging status by trust", () => {
    it("narrows the trust list in the imaging status panel", () => {
        cy.login();
        cy.intercept("GET", `/projects/${PROJECT_ID}`, { fixture: "project/getApprovedProject" });
        cy.intercept("GET", `/projects/${PROJECT_ID}/models*`, { body: { models: [], pagination: { totalCount: 0 } } });

        cy.visit(`/project/${PROJECT_ID}`);
        cy.demoPause();

        cy.getBySel("filter-project-status").scrollIntoView();
        cy.demoPause(400);

        cy.getBySel("filter-project-status").demoType("UCLH");
        cy.demoPause(1500);
    });
});
