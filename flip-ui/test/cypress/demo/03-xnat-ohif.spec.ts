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
// CYPRESS_BASE_URL pointed at the trust's XNAT (e.g. http://localhost:8105);
// the orchestrator resolves the XNAT project (matched on secondary_ID ==
// FLIP project id) and an experiment id before launching it. Navigating
// straight to /VIEWER/ keeps OHIF in the recorded tab — the XNAT UI's own
// launch link opens a new tab, which Cypress can't follow.

import { requireEnv, requireSecret } from "./support/demoFlow";

/* eslint-disable @typescript-eslint/no-explicit-any */

interface IViewportStack {
    cs: any;
    element: HTMLElement;
    stack: any;
}

// The viewport's image stack, as cornerstoneTools tracks it.
function viewportStack(win: any): IViewportStack {
    const cs = win.cornerstone;
    const element = cs.getEnabledElements()[0].element;
    const stack = win.cornerstoneTools.getToolState(element, "stack").data[0];

    return {
        cs,
        element,
        stack
    };
}

// World z of the middle labelled slice of the enrichment label (the label_*.nii.gz beside the
// scan's NIfTI input). The DICOM-SEG collection was built from this same mask, so it says where
// the segmentation will be drawn before the collection is imported.
async function labelledSliceZ(win: any, experimentId: string): Promise<number> {
    const scans = await (await win.fetch(`/data/experiments/${experimentId}/scans?format=json`)).json();
    for (const scan of scans.ResultSet.Result) {
        const files = await win.fetch(`/data/experiments/${experimentId}/scans/${scan.ID}/resources/NIFTI/files?format=json`);
        const label = files.ok ? (await files.json()).ResultSet.Result.find((f: any) => f.Name.startsWith("label_")) : null;
        if (!label) {
            continue;
        }
        let buffer: ArrayBuffer = await (await win.fetch(label.URI)).arrayBuffer();
        const magic = new Uint8Array(buffer, 0, 2);
        if (magic[0] === 0x1f && magic[1] === 0x8b) {
            const inflated = new win.Blob([buffer]).stream().pipeThrough(new win.DecompressionStream("gzip"));
            buffer = await new win.Response(inflated).arrayBuffer();
        }
        // NIfTI-1 header: dim[1..3] at 42, bitpix at 72, vox_offset at 108, sform_code at 254, srow_z at 312.
        const header = new DataView(buffer);
        const le = header.getInt32(0, true) === 348;
        expect(header.getInt16(254, le), "label sform_code").to.be.greaterThan(0);
        const [nx, ny, nz] = [42, 44, 46].map((offset) => header.getInt16(offset, le));
        const plane = nx * ny * (header.getInt16(72, le) / 8);
        const voxels = new Uint8Array(buffer, header.getFloat32(108, le));
        const labelled = Array.from({ length: nz }, (_, k) => k).filter((k) =>
            voxels.subarray(k * plane, (k + 1) * plane).some((byte) => byte !== 0)
        );
        expect(labelled, `labelled slices in ${label.Name}`).to.have.length.greaterThan(0);
        const k = labelled[Math.floor(labelled.length / 2)];

        return header.getFloat32(312 + 8, le) * k + header.getFloat32(312 + 12, le);
    }
    throw new Error(`No label_* NIfTI on ${experimentId} to locate the segmentation`);
}

// Step the viewport one slice at a time to `target`, the way a reader scrolls, rather than jumping.
async function stepToSlice(win: any, target: number, delayMs: number): Promise<void> {
    const { cs, element, stack } = viewportStack(win);
    const step = Math.sign(target - stack.currentImageIdIndex);
    for (let index = stack.currentImageIdIndex; index !== target; ) {
        index += step;
        stack.currentImageIdIndex = index;
        cs.displayImage(element, await cs.loadAndCacheImage(stack.imageIds[index]));
        await new Promise((resolve) => win.setTimeout(resolve, delayMs));
    }
}

// Scroll to the slice the segmentation crosses before importing it, so the mask appears the
// moment it is selected. The slice is matched on z (NIfTI and DICOM share the superior axis),
// not on index, which depends on how the viewer sorts the stack.
function scrollToLabelledSlice(experimentId: string): void {
    cy.window().then({ timeout: 120000 }, async (win: any) => {
        const z = await labelledSliceZ(win, experimentId);
        const { cs, stack } = viewportStack(win);
        const offsets = stack.imageIds.map((id: string) =>
            Math.abs(cs.metaData.get("imagePlaneModule", id).imagePositionPatient[2] - z)
        );
        await stepToSlice(win, offsets.indexOf(Math.min(...offsets)), 40);
    });
}

// Once imported, sweep through every slice the mask covers (to one end, the other, and back) so
// the segmentation reads as a volume. cornerstoneTools keys its labelmap by image index.
function scrollThroughSegmentation(): void {
    cy.window().then({ timeout: 120000 }, async (win: any) => {
        const { element, stack } = viewportStack(win);
        const labelmap = win.cornerstoneTools.getModule("segmentation").getters.labelmap3D(element, 0);
        const slices = Object.keys(labelmap?.labelmaps2D ?? {})
            .map(Number)
            .filter((index) => !Number.isNaN(index))
            .sort((a, b) => a - b);
        const middle = stack.currentImageIdIndex;
        for (const target of [slices[0], slices[slices.length - 1], middle]) {
            await stepToSlice(win, target, 70);
        }
    });
}

// Prove the slice in view carries the imported mask, so a z mismatch fails loudly instead of
// filming an empty viewport. cornerstoneTools keys its labelmap by image index.
function expectSegmentationInView(): void {
    cy.window().then((win: any) => {
        const { element, stack } = viewportStack(win);
        const labelmap = win.cornerstoneTools.getModule("segmentation").getters.labelmap3D(element, 0);
        expect(labelmap?.labelmaps2D?.[stack.currentImageIdIndex], "segmentation on the slice in view").to.not.equal(
            undefined
        );
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
        const xnatProjectId = requireEnv("DEMO_XNAT_PROJECT_ID");
        const xnatSubjectId = requireEnv("DEMO_XNAT_SUBJECT_ID");
        const xnatExperimentId = requireEnv("DEMO_XNAT_EXPERIMENT_ID");
        const xnatExperimentLabel = requireEnv("DEMO_XNAT_EXPERIMENT_LABEL");

        cy.visit("/");
        cy.demoCaption("Meanwhile, inside the trust: the cohort's imaging has been imported into XNAT", 1200);
        requireSecret("DEMO_XNAT_PASSWORD").then((password) => {
            cy.get("form#login_form", { timeout: 60000 }).within(() => {
                cy.get("input[name='username']").demoType(username);
                cy.get("input[name='password']").demoType(password, { log: false });
            });
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

        if (Cypress.expose("DEMO_XNAT_SEG_NAME")) {
            scrollToLabelledSlice(xnatExperimentId);
            cy.demoPause(600);
        }

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
        if (Cypress.expose("DEMO_XNAT_SEG_NAME")) {
            const collectionName = String(Cypress.expose("DEMO_XNAT_SEG_NAME"));
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
            expectSegmentationInView();
            scrollThroughSegmentation();
            cy.demoPause(800);
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
