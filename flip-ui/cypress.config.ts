/*
 * Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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




import { defineConfig } from "cypress";

export default defineConfig({
    projectId: "881dt2",
    viewportWidth: 1366,
    viewportHeight: 768,
    animationDistanceThreshold: 1,
    chromeWebSecurity: false,
    video: false,
    fixturesFolder: "test/cypress/fixtures",
    screenshotsFolder: "test/cypress/screenshots",
    videosFolder: "test/cypress/videos",
    downloadsFolder: "test/cypress/downloads",
    requestTimeout: 2000,
    defaultCommandTimeout: 5000,
    retries: {
        runMode: 3,
        openMode: 0
    },
    e2e: {
        // We've imported your old cypress plugins here.
        // You may want to clean this up later by importing these.
        setupNodeEvents(on, config) {
            return require("./test/cypress/plugins/index.ts").default(on, config);
        },
        baseUrl: "https://localhost:443",
        specPattern: "test/cypress/integration/**/*.spec.ts",
        supportFile: "test/cypress/support/index.ts",
        excludeSpecPattern: ["**/__snapshots__/*", "**/__image_snapshots__/*"]
    }
});
