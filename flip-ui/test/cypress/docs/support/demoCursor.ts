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

// Cypress runs headless and the browser doesn't render the OS cursor into
// the captured video. This module injects a CSS cursor overlay into the
// application-under-test window and animates it to each click/type target,
// so the recorded GIFs read as user actions instead of mystery DOM updates.
//
// The cursor lives in the AUT document, not the Cypress runner chrome — it
// only shows up in `cy.window()`'s document, which is what gets captured.

const CURSOR_ID = "cypress-demo-cursor";
const STYLE_ID = `${CURSOR_ID}-style`;
const MOVE_MS = 380;
const TYPE_DELAY_MS = 60;

// Tip of the arrow is roughly at (6, 6) in the 28x28 SVG viewBox.
const CURSOR_SVG = `
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 28 28" width="28" height="28">
  <path d="M5 3 L23 13 L14 14 L10 23 Z" fill="#111827" stroke="#ffffff" stroke-width="1.5"
        stroke-linejoin="round"/>
</svg>
`;

const STYLE = `
#${CURSOR_ID} {
  position: fixed;
  top: 0;
  left: 0;
  width: 28px;
  height: 28px;
  pointer-events: none;
  z-index: 2147483647;
  transform: translate3d(20px, 20px, 0);
  transition: transform ${MOVE_MS}ms cubic-bezier(.2,.7,.2,1);
  will-change: transform;
  filter: drop-shadow(0 1px 1px rgba(0,0,0,0.35));
}
#${CURSOR_ID}::after {
  content: "";
  position: absolute;
  top: 0;
  left: 0;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: rgba(59, 130, 246, 0.45);
  transform: scale(0);
  transform-origin: 6px 6px;
  opacity: 0;
}
#${CURSOR_ID}.cypress-demo-cursor-clicking::after {
  animation: cypress-demo-cursor-pulse 380ms ease-out;
}
@keyframes cypress-demo-cursor-pulse {
  0%   { transform: scale(0); opacity: 0.8; }
  100% { transform: scale(2.8); opacity: 0; }
}
`;

function ensureCursor(win: Window): HTMLElement {
    const doc = win.document;
    if (!doc.getElementById(STYLE_ID)) {
        const style = doc.createElement("style");
        style.id = STYLE_ID;
        style.textContent = STYLE;
        doc.head.appendChild(style);
    }
    let cursor = doc.getElementById(CURSOR_ID);
    if (!cursor) {
        cursor = doc.createElement("div");
        cursor.id = CURSOR_ID;
        cursor.innerHTML = CURSOR_SVG;
        doc.body.appendChild(cursor);
    }

    return cursor;
}

function moveCursorTo(win: Window, x: number, y: number): void {
    const cursor = ensureCursor(win);
    // Offset so the tip of the arrow sits on the target, not the top-left
    // corner of the SVG box.
    cursor.style.transform = `translate3d(${x - 6}px, ${y - 6}px, 0)`;
}

function pulse(win: Window): void {
    const cursor = ensureCursor(win);
    cursor.classList.remove("cypress-demo-cursor-clicking");
    // Force reflow so the animation restarts even when fired back-to-back.
    void cursor.getBoundingClientRect();
    cursor.classList.add("cypress-demo-cursor-clicking");
}

// Re-inject after every navigation — a fresh document wipes the overlay.
Cypress.on("window:load", (win: Window) => {
    ensureCursor(win);
});

// Demo specs never call .click(x, y) — only the no-arg or options-only form
// after a `cy.get(...)` / `cy.getBySel(...)` parent. Keeping the signature
// narrow side-steps the multi-overload typing dance documented by Cypress.
//
// Overriding `cy.click` / `cy.type` runs into Cypress 14's strict "callback
// returned a promise" guard the moment we mix the cursor animation chain with
// a returned originalFn chain. Custom child commands let the animation chain
// off the subject and end with the native `.click()` / `.type()`, side-stepping
// the override pattern entirely.

Cypress.Commands.add(
    "demoClick",
    { prevSubject: "element" },
    (subject: JQuery<HTMLElement>, options?: Partial<Cypress.ClickOptions>) => {
        cy.wrap(subject, { log: false }).then(($el) => {
            const win = ($el[0].ownerDocument.defaultView ?? window) as Window;
            const rect = $el[0].getBoundingClientRect();
            moveCursorTo(win, rect.left + rect.width / 2, rect.top + rect.height / 2);
        });
        cy.wait(MOVE_MS, { log: false });
        cy.wrap(subject, { log: false }).then(($el) => {
            const win = ($el[0].ownerDocument.defaultView ?? window) as Window;
            pulse(win);
        });
        cy.wait(120, { log: false });

        return cy.wrap(subject, { log: false }).click(options);
    }
);

Cypress.Commands.add(
    "demoType",
    { prevSubject: "element" },
    (subject: JQuery<HTMLElement>, text: string, options?: Partial<Cypress.TypeOptions>) => {
        cy.wrap(subject, { log: false }).then(($el) => {
            const win = ($el[0].ownerDocument.defaultView ?? window) as Window;
            const rect = $el[0].getBoundingClientRect();
            moveCursorTo(win, rect.left + rect.width / 2, rect.top + rect.height / 2);
        });
        cy.wait(MOVE_MS, { log: false });

        return cy.wrap(subject, { log: false }).type(text, { delay: TYPE_DELAY_MS, ...(options ?? {}) });
    }
);

Cypress.Commands.add("demoPause", (ms = 600) => {
    cy.wait(ms, { log: false });
});

declare global {
    // eslint-disable-next-line @typescript-eslint/no-namespace
    namespace Cypress {
        interface Chainable<Subject = unknown> {
            demoClick(options?: Partial<Cypress.ClickOptions>): Chainable<Subject>;
            demoType(text: string, options?: Partial<Cypress.TypeOptions>): Chainable<Subject>;
            demoPause(ms?: number): Chainable<void>;
        }
    }
}

export {};
