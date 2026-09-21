/**
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

/**
 * FLIP#995 — signing out must discard the page, not route within it.
 *
 * swrv's cache is module-level and never expires, and only the auth store is reset on
 * sign-out, so an SPA route push to /auth/login left the previous account's projects,
 * models and cohort results in memory for the next person to sign in on the same tab.
 * The unit test src/store/__tests__/signout-teardown.spec.ts reproduces that with the real
 * swrv. jsdom cannot prove the fix, because the fix is a real navigation: this spec does.
 *
 * The proof is a heap marker. A property planted on `window` survives any amount of
 * in-app routing and store resetting — it can only disappear if the document itself is
 * discarded and a fresh one loaded. If it is gone after sign-out, so is every cache,
 * store and closure the session held. A regression to a router push would fail the
 * marker assertion regardless of what else the page did.
 *
 * The companion path — a forced sign-out on token-refresh failure — is covered by
 * session_expired.spec.ts, which also proves the notice queued on the way out is
 * replayed on the fresh login page.
 */

const MARKER = "__flipHeapMarker";

describe("sign-out tears the page down", () => {
    beforeEach(() => {
        cy.login();
    });

    it("discards the running document, so nothing from the ended session survives in memory", () => {
        cy.visit("/projects");
        cy.contains("Projects").should("be.visible");

        // Plant the marker on the live window. In-app navigation keeps this window.
        cy.window().then((win) => {
            (win as unknown as Record<string, string>)[MARKER] = "planted-before-sign-out";
        });
        cy.window().its(MARKER).should("eq", "planted-before-sign-out");

        cy.getBySel("account-menu-btn").click();
        cy.getBySel("sign-out-btn").click();

        cy.url({ timeout: 10_000 }).should("include", "/auth/login");
        cy.contains("Sign in to FLIP").should("be.visible");

        // The document was replaced: the marker is gone, and with it the heap it lived in.
        cy.window().should((win) => {
            expect((win as unknown as Record<string, unknown>)[MARKER]).to.equal(undefined);
        });
    });
});
