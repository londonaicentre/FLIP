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



import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { THEME_COLOR_LIGHT } from "../composables/useThemeColorMeta";

const indexHtml = readFileSync(resolve(__dirname, "../../index.html"), "utf-8");

describe("index.html document metas", () => {
    it("does not declare viewport-fit=cover (inert in portrait, notch hazard in landscape)", () => {
        // Measured on iOS 26 Safari (#791): in portrait the page is letterboxed inside the browser
        // chrome regardless of viewport-fit, with safe-area insets reporting 0 — cover changes
        // nothing. In landscape, cover slides content under the notch, which would need
        // env(safe-area-inset-*) guards on every layout. Re-add cover only together with those.
        const viewport = indexHtml.match(/<meta name="viewport" content="([^"]*)"/);

        expect(viewport?.[1]).not.toContain("viewport-fit");
        expect(viewport?.[1]).toContain("width=device-width");
    });

    it("ships a pre-hydration theme-color matching the light page background", () => {
        expect(indexHtml).toContain(`<meta name="theme-color" content="${THEME_COLOR_LIGHT}"`);
    });
});
