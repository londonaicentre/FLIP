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

// Shared plumbing for the demo segments: real-Cognito login through the UI
// and the state file that hands project/model ids from one segment (one
// Cypress invocation) to the next via the orchestrator
// (flip-api/tests/demo_video.py).

export interface IDemoState {
    projectId?: string;
    modelId?: string;
}

// Written inside the repo bind-mount so the orchestrator on the host can
// read it between segments. Gitignored.
const STATE_FILE = "test/cypress/demo/state.json";

/** Read a required Cypress env var, failing the segment loudly when unset. */
export function requireEnv(name: string): string {
    const value = Cypress.env(name);
    if (!value) {
        throw new Error(`Missing required Cypress env var for demo segment: ${name}`);
    }

    return String(value);
}

/** Persist ids for the orchestrator + later segments. Full overwrite. */
export function saveDemoState(state: IDemoState): void {
    cy.writeFile(STATE_FILE, state);
}

// Stealth mode: some segments (training progress, results) should open
// directly on the dashboard rather than re-showing a sign-in. While active,
// every page load is hidden; the assembly's content-span trim cuts the
// uniform hidden frames, so the recorded clip starts at the reveal.
export const demoStealth = { active: false };

Cypress.on("window:before:load", (win) => {
    if (demoStealth.active) {
        win.document.documentElement.style.visibility = "hidden";
    }
});

/** End stealth mode and reveal the current page. */
export function revealDemo(): void {
    cy.window({ log: false }).then((win) => {
        demoStealth.active = false;
        win.document.documentElement.style.visibility = "";
    });
}

/** cy.visit that reliably hides the incoming page while stealth is active —
 * the global window:before:load hook alone can miss the first load. */
export function demoVisit(url: string): void {
    cy.visit(url, {
        onBeforeLoad(win) {
            if (demoStealth.active) {
                win.document.documentElement.style.visibility = "hidden";
            }
        }
    });
}

function decodeJwtPayload(win: Window, token: string): { sub: string; email?: string } {
    const part = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = part + "=".repeat((4 - (part.length % 4)) % 4);

    return JSON.parse(win.atob(padded));
}

// Real Cognito sign-in through the login form. `scenic` paces the typing and
// cursor for the on-camera opening; later segments log in fast to keep the
// final cut tight. Password fields are masked, so typing the real dev
// password on camera never exposes it.
//
// The dev server runs with VITE_E2E=true, which activates two Cypress test
// seams that shape what happens after the click:
//   1. Amplify v6's signIn promise wedges under the Cypress proxy AFTER the
//      SRP handshake has fully succeeded and the tokens are cached in
//      localStorage — so instead of waiting for the app's own redirect, we
//      poll for the cached tokens and load the app fresh.
//   2. The route guard's Cypress branch (src/utils/auth.ts) requires the
//      `cypress.auth.user` contract — we satisfy it with the REAL identity:
//      sub/email decoded from the real idToken, permissions fetched from the
//      hub with the real access token. Every subsequent API call rides the
//      genuine Cognito session via the axios interceptor.
Cypress.Commands.add("demoLogin", (email: string, password: string, options?: { scenic?: boolean; stealth?: boolean }) => {
    const scenic = options?.scenic ?? false;
    const stealth = options?.stealth ?? false;
    if (stealth) {
        demoStealth.active = true;
    }
    // flip-ui/.env.development carries the hub URL without the /api suffix,
    // the root env with it — normalise to a with-/api base either way.
    const rawHubUrl = String(Cypress.env("CENTRAL_HUB_API_URL") || "http://localhost:8080");
    const apiBase = rawHubUrl.replace(/\/api\/?$/, "") + "/api";
    demoVisit("/auth/login");
    if (scenic) {
        cy.getBySel("username").demoType(email);
        cy.getBySel("password").demoType(password, { log: false });
    } else {
        // force: in stealth mode the whole document is visibility:hidden.
        cy.getBySel("username").type(email, {
            delay: 0,
            force: true
        });
        cy.getBySel("password").type(password, {
            delay: 0,
            force: true,
            log: false
        });
    }
    if (stealth) {
        cy.getBySel("login-btn").click({ force: true });
    } else {
        cy.getBySel("login-btn").demoClick();
    }
    // The SRP round-trips take a couple of seconds — caption the wait so it
    // reads as intentional rather than a stall. The next segment caption
    // replaces this as soon as the app loads. When the orchestrator fell back
    // to the well-known admin for this persona (demo users not provisioned),
    // say so ON CAMERA — the CLI warning is invisible in the assembled mp4.
    const fallbackRole = Cypress.env("DEMO_CREDENTIALS_FALLBACK");
    cy.demoCaption(
        fallbackRole
            ? `Authenticating with the Central Hub… (demo ${fallbackRole} user not provisioned — ` +
                  "the well-known admin account plays this part)"
            : "Authenticating with the Central Hub…",
        0
    );

    // Real SRP against the real pool — wait for the token cache to land.
    cy.window({ timeout: 60000 }).should((win) => {
        const cached = Object.keys(win.localStorage).some((k) => k.endsWith(".accessToken"));
        expect(cached, "Cognito tokens cached in localStorage").to.eq(true);
    });

    cy.window().then((win) => {
        const keys = Object.keys(win.localStorage);
        const idToken = win.localStorage.getItem(keys.find((k) => k.endsWith(".idToken")) as string) as string;
        const accessToken = win.localStorage.getItem(
            keys.find((k) => k.endsWith(".accessToken")) as string
        ) as string;
        const identity = decodeJwtPayload(win, idToken);

        return cy
            .request({
                url: `${apiBase}/users/${identity.sub}/permissions`,
                headers: { Authorization: `Bearer ${accessToken}` }
            })
            .then((resp) => {
                win.localStorage.setItem(
                    "cypress.auth.user",
                    JSON.stringify({
                        username: identity.sub,
                        userId: identity.sub,
                        attributes: {
                            sub: identity.sub,
                            email: identity.email ?? email
                        },
                        permissions: resp.body?.permissions ?? []
                    })
                );
            });
    });

    demoVisit("/projects");
    cy.url({ timeout: 60000 }).should("include", "/projects");
});

declare global {
    // eslint-disable-next-line @typescript-eslint/no-namespace
    namespace Cypress {
        interface Chainable {
            demoLogin(
                email: string,
                password: string,
                options?: { scenic?: boolean; stealth?: boolean }
            ): Chainable<void>;
        }
    }
}
