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

import { demoStealth } from "./demoFlow";

// Each segment runs in a fresh browser, so the very first page paints in the
// fallback font and visibly swaps once the web fonts arrive (FOUT) — right
// at the start of the recording. Hide the first-loaded document until the
// declared font faces are actually loaded: the hidden frames are uniform,
// the assembly's content-span trim cuts them, and the segment opens
// fonts-correct. NOTE `document.fonts.ready` is useless here — on an SPA it
// resolves before the app has rendered any text (nothing is loading yet), so
// instead poll for the CSS-declared FontFace entries and force-load them
// all. Later navigations hit the font cache, so only the first load needs
// this.
let fontGuardApplied = false;
Cypress.on("window:before:load", (win) => {
    if (fontGuardApplied) {
        return;
    }
    fontGuardApplied = true;
    const doc = win.document;
    doc.documentElement.style.visibility = "hidden";
    const reveal = () => {
        // Stealth segments stay hidden until the spec reveals explicitly.
        if (demoStealth.active) {
            return;
        }
        doc.documentElement.style.visibility = "";
    };
    // Never hold the page hidden long — hard reveal after 4s regardless.
    win.setTimeout(reveal, 4000);
    const tryLoadFonts = (attempt: number) => {
        const faces = doc.fonts ? Array.from(doc.fonts) : [];
        if (faces.length === 0) {
            if (attempt < 35) {
                win.setTimeout(() => tryLoadFonts(attempt + 1), 100);
            } else {
                reveal();
            }

            return;
        }
        Promise.all(faces.map((face) => face.load().catch(() => undefined))).then(reveal, reveal);
    };
    tryLoadFonts(0);
});

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
