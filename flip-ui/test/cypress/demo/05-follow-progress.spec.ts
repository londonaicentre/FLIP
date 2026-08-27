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

// Demo segment 5 — training is live: the researcher follows progress on the
// model dashboard. The orchestrator only launches this once the first
// training metrics exist, so the charts have something to show. The sign-in
// happens in stealth (hidden, trimmed by the assembly) so the recorded clip
// opens directly on the dashboard — the assembly bridges from segment 4 with
// a blur transition instead of showing another login.

import { demoVisit, requireEnv, revealDemo } from "./support/demoFlow";

describe("FLIP demo — follow training progress", () => {
    it("shows the live training dashboard with metrics", () => {
        const email = requireEnv("DEMO_RESEARCHER_EMAIL");
        const password = requireEnv("DEMO_RESEARCHER_PASSWORD");
        const projectId = requireEnv("DEMO_PROJECT_ID");
        const modelId = requireEnv("DEMO_MODEL_ID");

        cy.demoLogin(email, password, { stealth: true });
        demoVisit(`/project/${projectId}/model/${modelId}`);
        // Everything is still hidden — wait on existence, then reveal onto a
        // fully-rendered dashboard.
        cy.getBySel("training-timeline", { timeout: 60000 }).should("exist");
        cy.get("canvas", { timeout: 120000 }).should("exist");
        revealDemo();

        cy.demoCaption("Later — training is running: weights travel to the hub, the data never does", 1000);
        cy.getBySel("training-timeline").should("be.visible");
        cy.demoPause(2500);

        cy.demoCaption("Live metrics stream back from every trust as the rounds progress", 600);
        cy.get("canvas").first().scrollIntoView();
        cy.get("canvas").first().should("be.visible");
        cy.demoPause(7000);
    });
});
