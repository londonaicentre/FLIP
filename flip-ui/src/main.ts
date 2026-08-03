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

import "tippy.js/dist/tippy.css";
import "tippy.js/themes/material.css";
import "./assets/styles/main.css";

import { Amplify } from "aws-amplify";
import { createPinia } from "pinia";
import { createApp } from "vue";
import VueTippy from "vue-tippy";
import SmartTable from "vuejs-smart-table";

import { makeDemoServer } from "../mocks/demo-server";
import { makeServer } from "../mocks/server";
import App from "./App.vue";
import { IS_DEMO, seedDemoAuth } from "./demo/bootstrap";
import router from "./router";
import { authConfig } from "./utils/auth";

const app = createApp(App);
const pinia = createPinia();
app.use(pinia);

Amplify.configure(authConfig);

// Cypress E2E uses its own `cy.intercept` network layer; running MirageJS
// alongside it produces racing handlers. The VITE_E2E gate is build-time
// (Vite inlines it), so non-E2E bundles never ship this branch and there's
// no runtime-flippable bypass to worry about.
const isE2E = import.meta.env.VITE_E2E === "true";

if (IS_DEMO) {
    // Public Ark+ demo: offline, read-only, mocked API with the recorded
    // (anonymised) platform register. Pin the axios baseURL to the prefix the
    // demo Mirage server answers on, so a stale window.js can't point the app
    // at a real backend, then seed the read-only viewer identity.
    console.info("Running in Ark+ demo mode — offline, read-only, mocked API.");
    window.AWS_BASE_URL = "/api";
    makeDemoServer();
    seedDemoAuth();
}
else if (import.meta.env.VITE_LOCAL === "true" && !isE2E) {
    console.info("Running locally, will use mocked API.");
    makeServer();
}

if (isE2E) {
    window.pinia = pinia;
}

app.use(router)
    .use(VueTippy, {
        defaultProps: {
            placement: "right",
            theme: "material"
        }
    })
    .use(SmartTable)
    .mount("#app");
