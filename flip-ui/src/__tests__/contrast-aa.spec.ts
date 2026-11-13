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

import resolveConfig from "tailwindcss/resolveConfig";
import { describe, expect, it } from "vitest";

import tailwindConfig from "../../tailwind.config.js";

// WCAG 2.2 AA guard over the colour pairs the UI relies on: 4.5:1 for text,
// 3:1 for borders, icons and focus rings. Colours come from the resolved config.
const colors = resolveConfig(tailwindConfig as never).theme.colors as Record<string, Record<string, string>>;

function luminance(hex: string): number {
    const [r, g, b] = [1, 3, 5].map((i) => {
        const c = parseInt(hex.slice(i, i + 2), 16) / 255;

        return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    });

    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(a: string, b: string): number {
    const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);

    return (hi + 0.05) / (lo + 0.05);
}

const white = "#ffffff";

describe("WCAG AA colour pairs", () => {
    it.each([
        ["placeholder gray-500 on white", colors.gray[500], white, 4.5],
        ["error text red-600 on white", colors.red[600], white, 4.5],
        ["white on APPROVED chip emerald-700", white, colors.emerald[700], 4.5],
        ["input border gray-500 on white", colors.gray[500], white, 3],
        ["status icon green-600 on white", colors.green[600], white, 3],
        ["dark table cell gray-400 on gray-800", colors.gray[400], colors.gray[800], 4.5],
        ["dark input border gray-500 on dark-raised", colors.gray[500], colors.dark.raised, 3],
        ["dark focus ring primary-400 on dark-canvas", colors.primary[400], colors.dark.canvas, 3]
    ])("%s", (_name, fg, bg, min) => {
        expect(ratio(fg, bg)).toBeGreaterThanOrEqual(min);
    });
});
