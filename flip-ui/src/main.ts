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

import { makeServer } from "../mocks/server";
import App from "./App.vue";
import { seedDemoAuth } from "./demo/bootstrap";
import router from "./router";
import { authConfig } from "./utils/auth";

const app = createApp(App);
const pinia = createPinia();
app.use(pinia);

// Cypress E2E uses its own `cy.intercept` network layer; running MirageJS
// alongside it produces racing handlers. The VITE_E2E gate is build-time
// (Vite inlines it), so non-E2E bundles never ship this branch and there's
// no runtime-flippable bypass to worry about.
const isE2E = import.meta.env.VITE_E2E === "true";

// The literal `import.meta.env.VITE_DEMO`, NOT the cross-module `IS_DEMO`
// re-export from ./demo/bootstrap — this gate is load-bearing for tree-shaking,
// not just for behaviour. vite.config.mts `define`s the flag in every build so
// rolldown can fold this to `false` and delete the branch below; a const
// imported from another module does not propagate far enough for that fold.
const isDemoBuild = import.meta.env.VITE_DEMO === "true";

// Amplify must not be configured in the demo: it would bind the demo to the
// REAL Cognito pool/client (same localStorage namespace on a same-origin
// deployment), so the demo's own Sign Out would issue a global sign-out
// against a genuine signed-in session in the same browser (FLIP#794 review).
if (!isDemoBuild) {
    Amplify.configure(authConfig);
}

/**
 * Install the mock API layer (if this build has one) and mount the app.
 *
 * The demo server is loaded through a DYNAMIC import inside the folded branch,
 * and that is a security control rather than a load-time optimisation. A static
 * `import { makeDemoServer } from "../mocks/demo-server"` is NOT dropped from a
 * production build even once this branch folds: demo-server.ts builds its
 * lookup tables at module scope with computed keys, which rolldown must treat
 * as side-effecting, so it keeps the module — and with it all 24 recorded
 * fixtures: production Cognito subs, the internal OMOP cohort SQL, the full
 * trust roster, every one of them readable by an anonymous `curl` of the public
 * entry chunk, whether or not the demo was ever deployed (FLIP#794 review).
 * Behind a dynamic import the reference disappears with the branch, so no
 * fixture chunk is emitted at all.
 *
 * Mounting waits for that install: Mirage patches XHR globally when it is
 * constructed, and the app issues its first requests during mount. Mounting
 * first would let those escape to the network before the mock exists. In a
 * non-demo build the whole branch folds away, leaving no `await` before the
 * mount, so production boot stays synchronous.
 *
 * Keep `assert-no-demo-artefacts` (npm postbuild:deploy) as the backstop for
 * all of the above — it inspects the built artefact, not the source.
 */
async function bootstrap(): Promise<void> {
    if (isDemoBuild) {
        // Public Ark+ demo: offline, read-only, mocked API with the recorded
        // (anonymised) platform register. Pin the axios baseURL to the prefix the
        // demo Mirage server answers on, so a stale window.js can't point the app
        // at a real backend, then seed the read-only viewer identity.
        console.info("Running in Ark+ demo mode — offline, read-only, mocked API.");
        window.AWS_BASE_URL = "/api";
        const { makeDemoServer } = await import("../mocks/demo-server");
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
}

void bootstrap();
