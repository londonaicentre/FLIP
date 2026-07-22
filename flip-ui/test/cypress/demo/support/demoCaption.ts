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

// Subtitle banner for the demo recordings. Injected into the AUT document
// (same pattern as docs/support/demoCursor.ts) so the caption is captured in
// the recorded video — no ffmpeg subtitle burn-in step needed. A follow-up
// cy.demoCaption("...") replaces the text; cy.demoCaption("") clears it.

const CAPTION_ID = "cypress-demo-caption";
const STYLE_ID = `${CAPTION_ID}-style`;
const FADE_MS = 260;

// The caption must survive full-page navigations, which wipe the AUT DOM.
// The current text is kept module-side and re-injected on window:load.
let currentText = "";

const STYLE = `
/* Demo-only click marker: make the cursor's click pulse unmistakable (the
   docs-GIF suite keeps its subtle one). This style tag is injected after
   demoCursor's, so these equal-specificity rules win the cascade. */
#cypress-demo-cursor::after {
  width: 26px;
  height: 26px;
  top: -7px;
  left: -7px;
  border-radius: 50%;
  border: 3px solid rgba(124, 58, 237, 0.95);
  background: rgba(124, 58, 237, 0.28);
  transform-origin: 13px 13px;
}
#cypress-demo-cursor.cypress-demo-cursor-clicking::after {
  animation-duration: 600ms;
}
#${CAPTION_ID} {
  position: fixed;
  left: 50%;
  bottom: 26px;
  transform: translateX(-50%);
  max-width: 76%;
  padding: 10px 24px;
  border-radius: 9999px;
  background: rgba(17, 24, 39, 0.85);
  color: #f9fafb;
  font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  font-size: 17px;
  line-height: 1.45;
  text-align: center;
  z-index: 2147483646;
  pointer-events: none;
  opacity: 0;
  transition: opacity ${FADE_MS}ms ease;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.35);
}
#${CAPTION_ID}.cypress-demo-caption-visible {
  opacity: 1;
}
`;

function ensureCaption(win: Window): HTMLElement {
    const doc = win.document;
    if (!doc.getElementById(STYLE_ID)) {
        const style = doc.createElement("style");
        style.id = STYLE_ID;
        style.textContent = STYLE;
        doc.head.appendChild(style);
    }
    let caption = doc.getElementById(CAPTION_ID);
    if (!caption) {
        caption = doc.createElement("div");
        caption.id = CAPTION_ID;
        doc.body.appendChild(caption);
    }

    return caption;
}

function renderCaption(win: Window, text: string): void {
    const caption = ensureCaption(win);
    if (text) {
        caption.textContent = text;
        caption.classList.add("cypress-demo-caption-visible");
    } else {
        caption.classList.remove("cypress-demo-caption-visible");
    }
}

// Re-inject after every navigation — a fresh document wipes the overlay.
Cypress.on("window:load", (win: Window) => {
    if (currentText) {
        renderCaption(win, currentText);
    }
});

Cypress.Commands.add("demoCaption", (text: string, holdMs = 900) => {
    cy.window({ log: false }).then((win) => {
        currentText = text;
        renderCaption(win, text);
    });
    if (holdMs > 0) {
        cy.wait(holdMs, { log: false });
    }
});

declare global {
    // eslint-disable-next-line @typescript-eslint/no-namespace
    namespace Cypress {
        interface Chainable {
            demoCaption(text: string, holdMs?: number): Chainable<void>;
        }
    }
}

export {};
