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

// Docs-only Cypress config. Runs the demo specs under test/cypress/docs/ with
// video recording on so scripts/videos-to-gifs.sh can turn each .mp4 into a
// docs GIF. The functional suite (cypress.config.ts) keeps video off so the
// 6-shard PR pipeline isn't slowed down.

import { defineConfig } from "cypress";

export default defineConfig({
    projectId: "881dt2",
    // 1280x800 downscaled to 1200px-wide GIFs maps cleanly to the 600px
    // figure width used throughout docs/source/sys-admin (2x for retina).
    viewportWidth: 1280,
    viewportHeight: 800,
    animationDistanceThreshold: 1,
    chromeWebSecurity: false,
    video: true,
    // Disable compression so ffmpeg has clean input for palette generation.
    videoCompression: false,
    // We don't care about failure screenshots for docs runs — the artefact
    // we want is the video.
    screenshotOnRunFailure: false,
    fixturesFolder: "test/cypress/fixtures",
    screenshotsFolder: "test/cypress/screenshots",
    videosFolder: "test/cypress/videos",
    downloadsFolder: "test/cypress/downloads",
    requestTimeout: 8000,
    defaultCommandTimeout: 8000,
    retries: {
        // A retry would re-record the spec, doubling the video. Demo specs
        // are pure happy-path so a flake means a stale recording, not a
        // hidden regression — fail loud and let the next push retry.
        runMode: 0,
        openMode: 0
    },
    e2e: {
        setupNodeEvents(on, config) {
            return require("./test/cypress/plugins/index.ts").default(on, config);
        },
        baseUrl: "http://localhost:4173",
        specPattern: "test/cypress/docs/**/*.spec.ts",
        supportFile: "test/cypress/docs/support/index.ts",
        excludeSpecPattern: ["**/__snapshots__/*", "**/__image_snapshots__/*"]
    }
});
