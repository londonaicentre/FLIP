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

// Demo segment 1 — the researcher signs in, creates a project, opens it,
// creates the cohort query from the project page, watches the aggregate
// results come back from the trusts, and stages the project for approval.
// Runs against the live stack; the admin approval is segment 2.

import { requireEnv, saveDemoState } from "./support/demoFlow";

// Minimal structural view of the CodeMirror 5 instance the editor element
// carries — avoids needing @types/codemirror in the Cypress TS build.
interface ICodeMirrorHost extends HTMLElement {
    CodeMirror: {
        setValue(value: string): void;
        refresh(): void;
    };
}

// Strip the Apache header comment block so the on-camera editor shows the
// query itself, not ten lines of licence boilerplate.
function stripLicenceHeader(sql: string): string {
    const lines = sql.split("\n");
    let start = 0;
    while (start < lines.length && (lines[start].startsWith("--") || lines[start].trim() === "")) {
        start += 1;
    }

    return lines.slice(start).join("\n").trim();
}

function setEditorValue(value: string): void {
    cy.get(".CodeMirror", { timeout: 30000 }).then(($cm) => {
        const editor = ($cm[0] as ICodeMirrorHost).CodeMirror;
        editor.setValue(value);
        editor.refresh();
    });
}

describe("FLIP demo — create project", () => {
    it("creates the project, runs the cohort query and stages the project", () => {
        const email = requireEnv("DEMO_RESEARCHER_EMAIL");
        const password = requireEnv("DEMO_RESEARCHER_PASSWORD");
        const projectName = requireEnv("DEMO_PROJECT_NAME");
        const projectDescription = requireEnv("DEMO_PROJECT_DESCRIPTION");
        const queryFile = requireEnv("DEMO_QUERY_FILE");

        cy.visit("/auth/login");
        cy.demoCaption("A researcher signs in to FLIP — the Federated Learning Interoperability Platform", 1400);
        cy.demoLogin(email, password, { scenic: true });

        cy.demoCaption("Creating a new research project", 800);
        cy.getBySel("add-project-btn").demoClick();
        // The list rows reuse some of these data-test hooks (e.g. the row
        // title is also project-name), so scope to the open modal — the
        // headlessui dialogs unmount when closed, leaving exactly one
        // [role=dialog] while the Create Project modal is up.
        cy.get("[role=dialog]").within(() => {
            cy.getBySel("project-name").demoType(projectName);
            cy.getBySel("project-description").demoType(projectDescription, { delay: 15 });
            cy.getBySel("create-project-btn").demoClick();
        });

        // The create call redirects to the new project's page — capture the
        // id for the orchestrator and the later segments, then let the page
        // breathe before the next action.
        cy.url({ timeout: 60000 })
            .should("match", /\/project\/[0-9a-f-]{36}$/)
            .then((url) => {
                saveDemoState({ projectId: url.split("/").pop() as string });
            });
        cy.demoCaption("The new project starts life unstaged, with no cohort defined yet", 800);
        cy.demoPause(2200);

        cy.demoCaption("Defining the patient cohort with an OMOP SQL query", 600);
        cy.getBySel("create-query-btn").scrollIntoView();
        cy.getBySel("create-query-btn").demoClick();

        // Write the query into the CodeMirror editor in a few sweeps so the
        // recording reads as authoring, not pasting.
        cy.readFile(queryFile).then((sql: string) => {
            const query = stripLicenceHeader(sql);
            const lines = query.split("\n");
            const chunks = 4;
            const perChunk = Math.ceil(lines.length / chunks);
            for (let i = 1; i <= chunks; i++) {
                setEditorValue(lines.slice(0, i * perChunk).join("\n"));
                cy.demoPause(400);
            }
        });

        cy.demoPause(1000);
        cy.demoCaption("The query runs inside each NHS trust — only aggregate counts leave the hospital", 800);
        cy.getBySel("view-cohort-query-results-btn").demoClick();
        cy.contains("Cohort Query Sent", { timeout: 90000 }).should("be.visible");

        // The trusts genuinely execute the query against their OMOP stores —
        // hold on the page until the aggregate charts render, then tour them.
        cy.demoCaption("Each trust runs the query against its own OMOP store and returns aggregates", 500);
        cy.get("canvas", { timeout: 180000 }).should("have.length.at.least", 1);
        cy.demoPause(1200);
        cy.get("canvas").first().scrollIntoView({ duration: 900 });
        cy.demoCaption("Aggregate cohort characteristics, per trust — no patient-level data", 500);
        cy.demoPause(3000);
        cy.get("canvas").last().scrollIntoView({ duration: 900 });
        cy.demoPause(2800);

        // Back on the project page, the researcher stages the project at the
        // trusts that answered — this is what goes to the admin for approval.
        cy.demoCaption("Staging the project at the trusts that returned a cohort", 800);
        cy.url().then((url) => cy.visit(url.replace(/\/cohort-query$/, "")));
        cy.get("[data-test$=\"-selector\"]", { timeout: 60000 })
            .should("have.length.at.least", 1)
            .each(($el) => {
                cy.wrap($el).scrollIntoView();
                cy.wrap($el).demoClick();
            });
        cy.getBySel("stage-project-btn").demoClick();

        // Staging refetches the project; the staging panel disappears once
        // the project is STAGED and awaiting admin approval.
        cy.getBySel("stage-project-btn", { timeout: 90000 }).should("not.exist");
        cy.demoCaption("The project is now staged, awaiting administrator approval", 500);
        cy.demoPause(3000);
    });
});
