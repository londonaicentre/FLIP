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

// Pointer-only companion to demoClick: glide the cursor overlay to a point on
// an element and dispatch a real mousemove there, without clicking.
//
// Beyond looking natural, this is load-bearing for the recording. Chrome feeds
// Cypress's video through a CDP screencast that only emits on compositor
// updates, so a page that renders once and then sits still (the OHIF viewer
// after it has drawn the study) contributes almost no frames — the beat can
// collapse to a fraction of its wall-clock length and lose the very frame that
// shows the image. Moving the cursor keeps frames flowing across the dwell.

const CURSOR_ID = "cypress-demo-cursor";
// Matches the cursor overlay's CSS transition in docs/support/demoCursor.ts.
const MOVE_MS = 450;

interface DemoHoverOptions {
    /** Horizontal position within the element, 0-1 (default centre). */
    fx?: number;
    /** Vertical position within the element, 0-1 (default centre). */
    fy?: number;
    /** How long to rest on the point once the cursor arrives. */
    dwellMs?: number;
}

Cypress.Commands.add(
    "demoHover",
    { prevSubject: "element" },
    (subject: JQuery<HTMLElement>, options: DemoHoverOptions = {}) => {
        const { fx = 0.5, fy = 0.5, dwellMs = 900 } = options;
        cy.wrap(subject, { log: false }).then(($el) => {
            const el = $el[0];
            const win = (el.ownerDocument.defaultView ?? window) as Window;
            const rect = el.getBoundingClientRect();
            const x = rect.left + rect.width * fx;
            const y = rect.top + rect.height * fy;
            const cursor = win.document.getElementById(CURSOR_ID);
            if (cursor) {
                // Same tip offset demoCursor applies, so the arrow point lands
                // on the target rather than its own top-left corner.
                cursor.style.transform = `translate3d(${x - 6}px, ${y - 6}px, 0)`;
            }
            el.dispatchEvent(new win.MouseEvent("mousemove", {
                clientX: x,
                clientY: y,
                bubbles: true
            }));
        });
        cy.wait(MOVE_MS + dwellMs, { log: false });

        return cy.wrap(subject, { log: false });
    }
);

declare global {
    // eslint-disable-next-line @typescript-eslint/no-namespace
    namespace Cypress {
        interface Chainable<Subject = unknown> {
            demoHover(options?: DemoHoverOptions): Chainable<Subject>;
        }
    }
}

export {};
