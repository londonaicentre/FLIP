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

import type { Plugin } from "vite";

const ICONS_PREFIX = "~icons/";
const RESOLVED_PREFIX = `\0${ICONS_PREFIX}`;

/**
 * Vitest-only replacement for unplugin-icons (FLIP#748).
 *
 * unplugin-icons serves every `~icons/<collection>/<name>` import (whether written by hand or generated
 * by unplugin-vue-components' IconsResolver) as a module the test runner fetches asynchronously. A
 * component render during a spec kicks off that fetch, the spec file can finish before it settles, and
 * the still-pending import then rejects against the torn-down environment — vitest records it under
 * "Unhandled Errors" (EnvironmentTeardownError) and exits 1 even though every test passed. Resolving
 * the whole `~icons/*` namespace to a synchronous inline stub removes that race for the entire icon
 * catalogue instead of patching individual specs.
 *
 * The stub renders `<svg data-icon="<collection>:<name>">`, so icon-presence assertions and snapshots
 * stay meaningful (the icon's identity is visible in the markup) and usage-site attributes such as
 * classes still fall through to the root element, exactly as with the real icon components.
 */
export default function iconStubPlugin(): Plugin {
    return {
        name: "flip-ui:vitest-icon-stub",
        enforce: "pre",
        resolveId(id) {
            if (id.startsWith(ICONS_PREFIX)) return `\0${id}`;
        },
        load(id) {
            if (!id.startsWith(RESOLVED_PREFIX)) return;
            const iconName = id.slice(RESOLVED_PREFIX.length).split("?")[0].replace(/\//g, ":");
            return [
                "import { h } from \"vue\";",
                "export default {",
                `    name: ${JSON.stringify(iconName)},`,
                `    render: () => h("svg", { "data-icon": ${JSON.stringify(iconName)} })`,
                "};"
            ].join("\n");
        }
    };
}
