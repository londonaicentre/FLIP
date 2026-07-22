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

// Demo segment 1 — the researcher signs in, creates a project and submits
// the cohort query to the trusts. Runs against the live stack; the
// orchestrator (flip-api/tests/demo_video.py) waits for the trusts to
// respond off-camera after this segment.

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

describe("FLIP demo — create project", () => {
    it("signs in, creates the project and submits the cohort query", () => {
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

        // The create call redirects to /project/<uuid> — capture the id for
        // the orchestrator and the later segments.
        cy.url({ timeout: 60000 })
            .should("match", /\/project\/[0-9a-f-]{36}$/)
            .then((url) => {
                const projectId = url.split("/").pop() as string;
                saveDemoState({ projectId });

                cy.demoCaption("Defining the patient cohort with an OMOP SQL query", 1200);
                cy.visit(`/project/${projectId}/cohort-query`);
            });

        cy.readFile(queryFile).then((sql: string) => {
            const query = stripLicenceHeader(sql);
            // The editor is CodeMirror 5 (AiCodeTextArea) — set the document
            // through the editor API so the wrapper propagates the value into
            // the form; typing ~60 lines of SQL char-by-char would eat the
            // whole segment.
            cy.get(".CodeMirror", { timeout: 30000 })
                .should("be.visible")
                .then(($cm) => {
                    const editor = ($cm[0] as ICodeMirrorHost).CodeMirror;
                    editor.setValue(query);
                    editor.refresh();
                });
        });

        cy.demoPause(1600);
        cy.demoCaption("The query runs inside each NHS trust — only aggregate counts leave the hospital", 800);
        cy.getBySel("view-cohort-query-results-btn").demoClick();

        // Trust round-trips are real: wait for the submission acknowledgement
        // and let the per-trust response panel settle on camera.
        cy.contains("Cohort Query Sent", { timeout: 90000 }).should("be.visible");
        cy.demoPause(3000);
    });
});
