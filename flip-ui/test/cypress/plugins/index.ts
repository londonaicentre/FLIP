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

import * as dotenv from "dotenv";

// Secrets. Reachable only through the async `cy.env([...])` command, which
// resolves in the Node process and never publishes the value to the browser.
// Only the demo harness has any of these — the functional suite mocks auth at
// the network layer and reads no env at all.
const SECRET_ENV_KEYS = [
    "DEMO_RESEARCHER_PASSWORD",
    "DEMO_ADMIN_PASSWORD",
    "DEMO_XNAT_PASSWORD"
];

// Non-sensitive run configuration, read synchronously via `Cypress.expose()`.
// Usernames and emails sit here deliberately: the demo segments type them into
// a visible field and the caption names the persona on camera, so they are
// public by construction. Passwords are the only thing the recording hides.
const PUBLIC_ENV_KEYS = [
    "CENTRAL_HUB_API_URL",
    "DEMO_ADMIN_EMAIL",
    "DEMO_APP_DIR",
    "DEMO_APP_FILES",
    "DEMO_BACKEND_LABEL",
    "DEMO_CREDENTIALS_FALLBACK",
    "DEMO_MODEL_DESCRIPTION",
    "DEMO_MODEL_ID",
    "DEMO_MODEL_NAME",
    "DEMO_PROJECT_DESCRIPTION",
    "DEMO_PROJECT_ID",
    "DEMO_PROJECT_NAME",
    "DEMO_QUERY_FILE",
    "DEMO_RESEARCHER_EMAIL",
    "DEMO_XNAT_EXPERIMENT_ID",
    "DEMO_XNAT_EXPERIMENT_LABEL",
    "DEMO_XNAT_PROJECT_ID",
    "DEMO_XNAT_SEG_NAME",
    "DEMO_XNAT_SUBJECT_ID",
    "DEMO_XNAT_USERNAME"
];

/** Copy just the named keys out of the process environment, skipping unset ones. */
function pickEnv(keys: string[]): Record<string, string> {
    const picked: Record<string, string> = {};
    for (const key of keys) {
        const value = process.env[key];
        if (value !== undefined && value !== "") {
            picked[key] = value;
        }
    }

    return picked;
}

export default function (
    on: Cypress.PluginEvents,
    config: Cypress.PluginConfigOptions
): void | Cypress.ConfigOptions | Promise<Cypress.ConfigOptions> {

    dotenv.config({ path: "./.env.e2e" });

    // This used to be `config.env = process.env`, which handed the whole shell
    // environment to Cypress.env() — and with `allowCypressEnv` on (the 15.x
    // default) any page JS in the app under test could read all of it. On a
    // demo run that meant the researcher/admin/XNAT passwords, plus whatever
    // else happened to be exported, including AWS credentials. Both configs
    // now set `allowCypressEnv: false`; these two allowlists are what replaces
    // it. Adding a key to a demo segment means adding it to one of them.
    config.env = pickEnv(SECRET_ENV_KEYS);
    config.expose = pickEnv(PUBLIC_ENV_KEYS);

    return config;
}
