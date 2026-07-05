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

import { describe, expect, it } from "vitest";

import { CHART_SERIES_COLORS, chartChrome, seriesStyle } from "@/components/AiChart/chartTheme";

describe("chartTheme", () => {
    it("provides eight brand-anchored series slots per mode", () => {
        expect(CHART_SERIES_COLORS.light).toHaveLength(8);
        expect(CHART_SERIES_COLORS.dark).toHaveLength(8);
        // Slot 1 is the AI Centre plum, stepped per mode for legibility
        // (primary-400 on white; a lighter step on the dark surface).
        expect(CHART_SERIES_COLORS.light[0]).toBe("#9452A8");
        expect(CHART_SERIES_COLORS.dark[0]).toBe("#A873BB");
    });

    it("assigns series colours in fixed slot order per mode", () => {
        expect(seriesStyle(0, false)).toEqual({
            color: "#9452A8",
            lineType: "solid"
        });
        expect(seriesStyle(1, false)).toEqual({
            color: "#1baf7a",
            lineType: "solid"
        });
        expect(seriesStyle(4, true)).toEqual({
            color: "#3987e5",
            lineType: "solid"
        });
    });

    it("distinguishes overflow series with a dashed line instead of a bare colour repeat", () => {
        expect(seriesStyle(8, false)).toEqual({
            color: "#9452A8",
            lineType: "dashed"
        });
        expect(seriesStyle(9, true)).toEqual({
            color: "#199e70",
            lineType: "dashed"
        });
    });

    it("keeps the plot chrome transparent and on the FLIP surface/ink tokens", () => {
        const light = chartChrome(false);
        const dark = chartChrome(true);

        expect(light.background).toBe("transparent");
        expect(dark.background).toBe("transparent");
        expect(dark.gridLine).toBe("#2A2336"); // dark-border
        expect(dark.axisLine).toBe("#3A3147"); // dark-border-strong
        expect(light.axisLabel).toBe("#6B7280"); // gray-500 — 4.8:1 on white

        // The off-token fills/inks Lighthouse + dark-mode review flagged must not resurface.
        const legacyHexes = ["#111827", "#282A36", "#4A5462", "#ccc"];
        for (const chrome of [light, dark]) {
            for (const value of Object.values(chrome)) {
                expect(legacyHexes).not.toContain(value);
            }
        }
    });
});
