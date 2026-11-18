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

// A visitor's walk through the built public demo (FLIP#1746): every request is
// answered by the bundle's own Mirage server, so nothing here is intercepted.
// Run against `npm run serve:demo`; see cypress.public-demo.config.ts.

import arkModels from "../../../mocks/demo/projects/arkplus-finetune/data/models.json";
import arkProject from "../../../mocks/demo/projects/arkplus-finetune/data/project.json";
import ehrProject from "../../../mocks/demo/projects/ehr-risk/data/project.json";

const PROJECTS = [
    "Ark+ Finetuning (2-trust, 50r)",
    "Ark+ Baseline Eval (2-trust)",
    "EHR risk prediction",
    "Spleen 3D segmentation",
    "Spleen segmentation evaluation"
];
// Ids come from the fixtures: they are the demo's own (re-keyed by scripts/demo-fixtures.mjs).
const EHR_PROJECT = `/demo/project/${ehrProject.id}`;
const ARK_MODEL = `/demo/project/${arkProject.id}/model/${arkModels.data[0].id}`;
const SITES = ["Guy's and St Thomas' Trust", "King's College Hospital"];
const LOAD = { timeout: 15000 };

describe("public demo walk", () => {
    it("lists five projects and opens each", () => {
        cy.visit("/demo/projects");
        cy.get("[data-test='demo-banner']", LOAD).should("contain.text", `${PROJECTS.length} federated projects`);
        for (const name of PROJECTS) {
            cy.contains(name, LOAD).should("be.visible");
        }
        for (const name of PROJECTS) {
            cy.visit("/demo/projects");
            cy.contains(name, LOAD).click();
            cy.location("pathname", LOAD).should("match", /^\/demo\/project\/[0-9a-f-]{36}/);
            cy.contains(name, LOAD).should("be.visible");
            cy.contains("Something went wrong").should("not.exist");
        }
    });

    it("renders a project deep link on a cold load", () => {
        cy.visit(EHR_PROJECT);
        cy.contains("EHR risk prediction", LOAD).should("be.visible");
        cy.contains("Something went wrong").should("not.exist");
    });

    it("shows Connection Status with the two demo sites online", () => {
        cy.visit("/demo/ConnectionStatus");
        // The topology view renders a hidden copy of each name: match visible text only.
        for (const site of SITES) {
            cy.contains(":visible", site, LOAD).should("be.visible");
        }
        cy.contains("All trusts reporting healthy", LOAD).should("be.visible");
        cy.contains("Bangkok").should("not.exist");
    });

    it("shows no download control on a model page", () => {
        cy.visit(ARK_MODEL);
        // A finished run opens on the Run tab, where the results menu would live.
        cy.contains("Ark+ Finetuning 50r client_api", LOAD).should("be.visible");
        cy.get("[data-test='download-results-btn']").should("not.exist");
        // Model files are on the Prepare tab.
        cy.get("[data-test='tab-prepare']").click();
        cy.contains("Model Files", LOAD).should("be.visible");
        cy.get("[data-test='download-all-files-btn']").should("not.exist");
        cy.get("[aria-label^='Download ']").should("not.exist");
    });
});
