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

// Records docs/source/assets/flip/stage-project.gif.
// On a project with a cohort but not yet staged: select the participating
// trusts, click "Stage Project", confirm. Trust names come from the global
// trust/all fixture (UCLH, KCH).

const PROJECT_ID = "6fcbdd40-3675-45c9-899e-1a005e5245ba";

describe("docs: stage project", () => {
    it("stages a project across both trusts", () => {
        cy.login();
        cy.intercept("GET", `/projects/${PROJECT_ID}`, {
            fixture: "project/getProjectWithQueryNotApprovedUnstaged"
        });
        cy.intercept("GET", `/projects/${PROJECT_ID}/models*`, { body: { models: [], pagination: { totalCount: 0 } } });
        cy.intercept("POST", `/projects/${PROJECT_ID}/stage`, { statusCode: 200, body: {} }).as("stageProject");

        cy.visit(`/project/${PROJECT_ID}`);
        cy.demoPause();

        cy.getBySel("UCLH-selector").demoClick();
        cy.demoPause(300);

        cy.getBySel("KCH-selector").demoClick();
        cy.demoPause();

        cy.getBySel("stage-project-btn").demoClick();
        cy.wait("@stageProject");
        cy.demoPause(1500);
    });
});
