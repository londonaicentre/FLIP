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



import { beforeEach, describe, expect, it } from "vitest";
import { nextTick, ref } from "vue";

import useThemeColorMeta, { THEME_COLOR_DARK, THEME_COLOR_LIGHT } from "../useThemeColorMeta";

const getMeta = () => document.head.querySelector<HTMLMetaElement>("meta[name=\"theme-color\"]");

describe("useThemeColorMeta", () => {
    beforeEach(() => {
        getMeta()?.remove();
    });

    it("creates the meta tag and applies the light page colour immediately", () => {
        useThemeColorMeta(ref(false));

        expect(getMeta()?.content).toBe(THEME_COLOR_LIGHT);
    });

    it("applies the dark page colour when the theme is dark", () => {
        useThemeColorMeta(ref(true));

        expect(getMeta()?.content).toBe(THEME_COLOR_DARK);
    });

    it("tracks theme toggles", async () => {
        const isDark = ref(false);
        useThemeColorMeta(isDark);

        isDark.value = true;
        await nextTick();

        expect(getMeta()?.content).toBe(THEME_COLOR_DARK);
    });

    it("reuses the static meta tag from index.html instead of adding a second one", () => {
        const existing = document.createElement("meta");
        existing.name = "theme-color";
        existing.content = "#000000";
        document.head.appendChild(existing);

        useThemeColorMeta(ref(true));

        expect(document.head.querySelectorAll("meta[name=\"theme-color\"]").length).toBe(1);
        expect(existing.content).toBe(THEME_COLOR_DARK);
    });
});
