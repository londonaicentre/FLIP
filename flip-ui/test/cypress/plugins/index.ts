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





// eslint-disable-next-line @typescript-eslint/no-var-requires
const path = require("path");
import { startDevServer } from "@cypress/vite-dev-server";
import * as dotenv from "dotenv";


export default function (
    on: Cypress.PluginEvents,
    config: Cypress.PluginConfigOptions
): void | Cypress.ConfigOptions | Promise<Cypress.ConfigOptions> {

    dotenv.config({ path: "./.env.development" });
    config.env = process.env;

    on("dev-server:start", async (options: Cypress.DevServerConfig) => {
        return startDevServer({
            options,
            viteConfig: { configFile: path.resolve(__dirname, "..", "..", "vite.config.js"), },
        });
    });

    return config;
}
