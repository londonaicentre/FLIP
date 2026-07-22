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

// Demo-suite support entry. Unlike the docs suite this deliberately does NOT
// import globalIntercepts — every request must reach the live dev stack
// (real Cognito, real flip-api, real trusts, real S3 presigns) so the
// recorded walkthrough is genuine end-to-end behaviour, not fixtures.

import "../../support/commands";
import "cypress-file-upload";
import "cypress-localstorage-commands";
import "../../docs/support/demoCursor";
import "./demoCaption";
import "./demoFlow";

// Same runner-chrome hiding as the docs suite (see the rationale and the
// Cypress-version pinning note in test/cypress/docs/support/index.ts): the
// captured mp4 contains the whole runner window, so the command-log sidebar
// is hidden via CSS and the residual bezel is cropped by
// scripts/assemble-demo-video.sh.
before(() => {
    const top = window.parent;
    if (!top || !top.document || top.document.getElementById("docs-aut-fullbleed")) {
        return;
    }
    const style = top.document.createElement("style");
    style.id = "docs-aut-fullbleed";
    style.textContent = `
        .reporter-wrap, .reporter, [data-cy="reporter"],
        .specs-list-container, .specs-list,
        [data-cy="runnable-header"], .runnable-header,
        .toggle-specs-text, .reporter-running-row,
        body > div.toggle-specs-text {
            display: none !important;
        }
    `;
    top.document.head.appendChild(style);
});
