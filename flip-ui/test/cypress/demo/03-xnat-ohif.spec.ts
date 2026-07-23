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

// Demo segment 3 — inside one trust: the imported cohort has landed in XNAT,
// and a study is opened in the OHIF DICOM viewer. This segment runs with
// CYPRESS_BASE_URL pointed at the trust's XNAT (e.g. http://localhost:8104);
// the orchestrator resolves the XNAT project (matched on secondary_ID ==
// FLIP project id) and an experiment id before launching it. Navigating
// straight to /VIEWER/ keeps OHIF in the recorded tab — the XNAT UI's own
// launch link opens a new tab, which Cypress can't follow.

import { requireEnv } from "./support/demoFlow";

// Scroll the viewer to a slice that actually carries the imported segmentation,
// and prove one was found. The mask occupies a contiguous run of slices that is
// nowhere near the middle of the stack (a spleen sits at the bottom of an
// abdominal CT), so picking a slice by guesswork films an empty viewport.
// cornerstoneTools keys its labelmap by image index, which is the ground truth.
function scrollToSegmentedSlice(): void {
    cy.window().then((win) => {
        /* eslint-disable @typescript-eslint/no-explicit-any */
        const cs = (win as any).cornerstone;
        const csTools = (win as any).cornerstoneTools;
        const enabled = cs.getEnabledElements()[0];
        const element = enabled.element;
        const stack = csTools.getToolState(element, "stack").data[0];
        const labelmap = csTools.getModule("segmentation").getters.labelmap3D(element, 0);
        const slices = Object.keys(labelmap?.labelmaps2D ?? {})
            .map(Number)
            .filter((index) => !Number.isNaN(index))
            .sort((a, b) => a - b);
        expect(slices, "slices carrying the imported segmentation").to.have.length.greaterThan(0);

        const target = slices[Math.floor(slices.length / 2)];
        stack.currentImageIdIndex = target;

        return cs.loadAndCacheImage(stack.imageIds[target]).then((image: unknown) => {
            cs.displayImage(element, image);
        });
    });
}

// XNAT enforces a per-user session cap and pops a blocking "User session
// ended" dialog when an older session gets culled mid-page. Dismiss it if
// present so it never lingers in the recording.
function dismissSessionDialog(): void {
    cy.get("body").then(($body) => {
        const ok = $body.find("button:contains('OK'):visible");
        if (ok.length) {
            cy.wrap(ok.first()).click({ force: true });
        }
    });
}

// Dismissing the dialog does not always clear XNAT's timeout widget: when it
// reads a stale expiration cookie it greys the whole page out behind a
// "Session Expired" mask, which then films as a washed-out screen. A fresh
// full-page load re-stamps the cookie and repaints; do it only when the mask
// is actually up so the happy path costs nothing.
function clearSessionMask(): void {
    dismissSessionDialog();
    cy.get("body").then(($body) => {
        if ($body.text().includes("Session Expired")) {
            cy.reload();
            dismissSessionDialog();
        }
    });
}

describe("FLIP demo — XNAT + OHIF at the trust", () => {
    // XNAT's Velocity pages and the OHIF bundle both throw benign script
    // errors that would otherwise fail the segment.
    Cypress.on("uncaught:exception", () => false);

    it("logs in to the trust XNAT and views an imported DICOM in OHIF", () => {
        const username = requireEnv("DEMO_XNAT_USERNAME");
        const password = requireEnv("DEMO_XNAT_PASSWORD");
        const xnatProjectId = requireEnv("DEMO_XNAT_PROJECT_ID");
        const xnatSubjectId = requireEnv("DEMO_XNAT_SUBJECT_ID");
        const xnatExperimentId = requireEnv("DEMO_XNAT_EXPERIMENT_ID");
        const xnatExperimentLabel = requireEnv("DEMO_XNAT_EXPERIMENT_LABEL");

        cy.visit("/");
        cy.demoCaption("Meanwhile, inside the trust: the cohort's imaging has been imported into XNAT", 1200);
        cy.get("form#login_form", { timeout: 60000 }).within(() => {
            cy.get("input[name='username']").demoType(username);
            cy.get("input[name='password']").demoType(password, { log: false });
        });
        cy.get("form#login_form [type='submit'], form#login_form button").first().demoClick();

        // XNAT's post-login landing page — hold long enough to take it in.
        cy.url({ timeout: 60000 }).should("not.include", "Login.vm");
        // Under a scripted login XNAT's timeout widget can read a stale
        // session-expiration cookie and grey the page out as "expired"; a
        // fresh full-page load re-stamps the cookie and clears the overlay.
        cy.reload();
        cy.demoCaption("The trust's XNAT home — imaging lives here, inside the hospital", 600);
        clearSessionMask();
        // Wait for the home page to be populated, not merely done loading —
        // asserting only that the "Loading recent data" placeholder is gone
        // also passes on a blank page that never rendered its panels.
        cy.contains("Recent Data Activity", { timeout: 60000 }).should("be.visible");
        cy.contains("Loading recent data", { timeout: 60000 }).should("not.exist");
        cy.demoPause(4500);

        cy.demoCaption("Each FLIP project maps to an XNAT project holding its imported studies", 600);
        cy.visit(
            "/app/action/DisplayItemAction/search_value/" +
                `${xnatProjectId}/search_element/xnat%3AprojectData/search_field/xnat%3AprojectData.ID`
        );
        cy.demoPause(2000);
        clearSessionMask();
        cy.demoPause(2500);

        cy.demoCaption("Reviewing an imported study in the OHIF DICOM viewer", 600);
        // Same query-string shape the plugin's own "View Session" action
        // builds (see /scripts/xnat/plugin/ohif-viewer/viewer.js) — the
        // viewer rejects the URL without subjectId.
        cy.visit(
            `/VIEWER/?subjectId=${xnatSubjectId}&projectId=${xnatProjectId}` +
                `&experimentId=${xnatExperimentId}&experimentLabel=${encodeURIComponent(xnatExperimentLabel)}`
        );
        // Assert on the viewer's own chrome, not just any <canvas>: a session
        // bounce lands back on an XNAT page, and this beat is the whole point
        // of the segment — it must fail loudly rather than film the wrong page.
        cy.contains(/back to xnat/i, { timeout: 120000 }).should("be.visible");
        // OHIF pulls series metadata + pixel data before first render; the
        // study viewport is the last canvas on the page (the series thumbnail
        // is the first, and it paints well before the viewport does).
        cy.get("canvas", { timeout: 180000 }).should("be.visible");
        cy.get("canvas").last().should("be.visible");

        // Once OHIF has drawn the study the page is completely static, and
        // Chrome only feeds the screencast on compositor updates — a still
        // page records as almost no frames, which is how an earlier take shipped
        // a black viewport. Walking the pointer over the image keeps frames
        // coming (and reads as a clinician reviewing the study).
        cy.get("canvas").last().demoHover({
            fx: 0.42,
            fy: 0.3,
            dwellMs: 1200
        });
        cy.get("canvas").last().demoHover({
            fx: 0.55,
            fy: 0.5,
            dwellMs: 1400
        });
        cy.get("canvas").last().demoHover({
            fx: 0.45,
            fy: 0.68,
            dwellMs: 1400
        });
        cy.get("canvas").last().demoHover({
            fx: 0.6,
            fy: 0.42,
            dwellMs: 1600
        });

        // Segmentation apps only: the labels added during data enrichment are also
        // published as a DICOM-SEG ROI collection, which the viewer draws over the
        // images once imported. Classification demos have nothing to import, so the
        // orchestrator only sets this when the session actually carries a collection.
        if (Cypress.env("DEMO_XNAT_SEG_NAME")) {
            const collectionName = String(Cypress.env("DEMO_XNAT_SEG_NAME"));
            cy.demoCaption("The labels added during data enrichment travel with the imaging", 600);
            cy.contains("Masks").demoClick();
            cy.demoPause(1200);
            cy.contains("Import").demoClick();
            cy.demoPause(1600);
            cy.contains(collectionName, { timeout: 60000 }).demoClick();
            cy.demoPause(600);
            cy.contains("Import selected").demoClick();

            cy.demoCaption("Segmentation overlaid on the source CT — never leaving the trust", 600);
            // The import decodes the SEG frame by frame; the labelmap is only
            // complete once that settles.
            cy.demoPause(7000);
            scrollToSegmentedSlice();
            cy.demoPause(1500);
            cy.get("canvas").last().demoHover({
                fx: 0.5,
                fy: 0.45,
                dwellMs: 1800
            });
            cy.get("canvas").last().demoHover({
                fx: 0.58,
                fy: 0.55,
                dwellMs: 2200
            });
        }
    });
});
