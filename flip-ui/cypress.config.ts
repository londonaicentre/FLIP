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




import { defineConfig } from "cypress";

export default defineConfig({
    projectId: "881dt2",
    // Cypress 15: Cypress.env() publishes its values to any page JS in the app
    // under test. Nothing here uses it any more — non-sensitive run config comes
    // from Cypress.expose() and secrets from cy.env() — so turn the channel off
    // rather than leave it open. See test/cypress/plugins/index.ts.
    allowCypressEnv: false,
    viewportWidth: 1366,
    viewportHeight: 768,
    animationDistanceThreshold: 1,
    chromeWebSecurity: false,
    video: false,
    fixturesFolder: "test/cypress/fixtures",
    screenshotsFolder: "test/cypress/screenshots",
    videosFolder: "test/cypress/videos",
    downloadsFolder: "test/cypress/downloads",
    // 8s is enough for cy.wait("@alias") to absorb the Vue + SWRV mount
    // chain even under load (Vite HMR connection, Pinia hydration, layout
    // SWRV resolving the project before the inner component fires its
    // own fetch). The historical 2s value was tight and timed out under
    // CI's slower-than-local cold start.
    requestTimeout: 8000,
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
        baseUrl: "http://localhost:4173",
        specPattern: "test/cypress/integration/**/*.spec.ts",
        supportFile: "test/cypress/support/index.ts",
        excludeSpecPattern: ["**/__snapshots__/*", "**/__image_snapshots__/*"]
    }
});
