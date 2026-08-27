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

// Demo-video Cypress config. Runs the end-to-end demo segments under
// test/cypress/demo/ against the LIVE dev stack — real Cognito, real
// flip-api, real trusts — with video recording on, so that
// scripts/assemble-demo-video.sh can crop and concatenate the segment mp4s
// into a single walkthrough video (driven by flip-api/tests/demo_video.py).
//
// Fork of cypress.docs.config.ts. The viewport + --window-size pairing MUST
// stay identical to the docs config: both recordings feed the same ffmpeg
// crop constants (1280x800 at offset 540,80 — see scripts/videos-to-gifs.sh
// and scripts/assemble-demo-video.sh).

import { defineConfig } from "cypress";

// Render the browser at N× device pixels for a high-resolution recording
// (DEMO_VIDEO_SCALE=3 → the 1280x800 viewport captures at 3840x2400, i.e.
// 4K-class). The window size below stays in DIPs; Chrome multiplies the
// framebuffer, and scripts/assemble-demo-video.sh scales its crop window by
// the same factor.
const videoScale = Math.max(1, Number(process.env.DEMO_VIDEO_SCALE || "1") || 1);

export default defineConfig({
    // 1280x800 viewport rendered 1:1 inside a 1920x1200 Chrome window; the
    // assembly script crops the AUT viewport back out of the capture.
    viewportWidth: 1280,
    viewportHeight: 800,
    animationDistanceThreshold: 1,
    chromeWebSecurity: false,
    video: true,
    // Disable compression so ffmpeg gets clean input for the final encode.
    videoCompression: false,
    // Unlike docs runs these hit a live stack — a failure screenshot is
    // genuinely useful for diagnosing what state the stack was in.
    screenshotOnRunFailure: true,
    fixturesFolder: "test/cypress/fixtures",
    screenshotsFolder: "test/cypress/demo/screenshots",
    videosFolder: "test/cypress/demo/videos",
    downloadsFolder: "test/cypress/demo/downloads",
    // Each demo segment is its own `cypress run`; the default trashing would
    // delete every earlier segment's mp4 before recording the next one.
    trashAssetsBeforeRuns: false,
    // The live stack answers at real-world speeds (Cognito sign-in, trust
    // round-trips, S3 uploads), so demo steps wait far longer than the
    // fully-mocked docs suite ever needs to.
    requestTimeout: 30000,
    defaultCommandTimeout: 30000,
    pageLoadTimeout: 120000,
    retries: {
        // A retry would re-record the segment, corrupting the final cut.
        // Fail loud; the orchestrator reruns the segment explicitly.
        runMode: 0,
        openMode: 0
    },
    e2e: {
        setupNodeEvents(on, config) {
            // Same require-based plugin load as cypress.docs.config.ts — the
            // plugins file registers the dotenv → Cypress.env passthrough.
            // eslint-disable-next-line @typescript-eslint/no-require-imports
            const result = require("./test/cypress/plugins/index.ts").default(on, config);
            // Same window bump as cypress.docs.config.ts: headless Chrome's
            // default 1280x720 window would force the AUT iframe to scale
            // down; 1920x1200 gives it room to render 1:1 at viewport size
            // so the fixed crop in scripts/assemble-demo-video.sh lines up.
            on("before:browser:launch", (browser, launchOptions) => {
                if (browser.family === "chromium") {
                    const sized = launchOptions.args.filter(
                        (arg) => !arg.startsWith("--window-size=")
                    );
                    sized.push("--window-size=1920,1200");
                    if (videoScale > 1) {
                        sized.push(`--force-device-scale-factor=${videoScale}`);
                    }
                    launchOptions.args = sized;
                }

                return launchOptions;
            });

            return result;
        },
        // The live dev UI. Overridable per segment with CYPRESS_BASE_URL —
        // the XNAT/OHIF segment points this at a trust's XNAT instead.
        baseUrl: "http://localhost:44357",
        specPattern: "test/cypress/demo/**/*.spec.ts",
        supportFile: "test/cypress/demo/support/index.ts",
        excludeSpecPattern: ["**/__snapshots__/*", "**/__image_snapshots__/*"]
    }
});
