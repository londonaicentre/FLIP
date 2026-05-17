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

// Docs-suite support entry. Loads the same commands and global intercepts as
// the functional suite, then layers the demo cursor + pacing on top.

import "../../support/commands";
import "../../support/globalIntercepts";
import "cypress-file-upload";
import "cypress-localstorage-commands";
import "./demoCursor";

// Cypress 14 headless captures the whole runner window (command-log sidebar,
// URL bar, AUT iframe) into the recorded mp4 rather than the AUT viewport only.
// This injects CSS into the runner document to hide the command-log sidebar so
// the AUT iframe takes most of the recording. The residual dark bezel + URL bar
// strip get cropped in scripts/videos-to-gifs.sh before encoding to GIF.
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
