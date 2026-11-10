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

// Walks the built public demo (FLIP#1746) served by `npm run serve:demo` at
// http://127.0.0.1:4174/demo/. No login, no intercepts: the bundle's own
// MirageJS server answers every request. Local tool, not run in CI — see
// README.md "Public demo build".
import { defineConfig } from "cypress";

export default defineConfig({
    allowCypressEnv: false,
    viewportWidth: 1280,
    viewportHeight: 800,
    video: false,
    screenshotOnRunFailure: true,
    screenshotsFolder: "test/cypress/screenshots",
    requestTimeout: 8000,
    defaultCommandTimeout: 8000,
    retries: {
        runMode: 0,
        openMode: 0
    },
    e2e: {
        baseUrl: "http://127.0.0.1:4174",
        specPattern: "test/cypress/public-demo/**/*.cy.ts",
        supportFile: false,
        excludeSpecPattern: ["**/__snapshots__/*", "**/__image_snapshots__/*"]
    }
});
