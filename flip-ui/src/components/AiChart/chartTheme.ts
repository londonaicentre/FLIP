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

/**
 * Shared ECharts theme for every FLIP chart (model metrics, cohort results).
 *
 * Series palette (issue #716): brand-anchored — slot 1 is the AI Centre plum,
 * stepped per mode (primary-400 on white; a lighter step on the dark surface).
 * Both mode columns carry the same hues in the same order; the ordering was
 * chosen by maximising the minimum adjacent colour-vision-deficiency distance
 * (min adjacent deltaE 37.7 light / 35.9 dark under protan/deutan simulation,
 * target >= 12) and validated against the real chart surfaces (white /
 * dark-surface #0E0B13) for OKLCH lightness band, chroma floor and contrast.
 * Light-mode aqua/yellow/magenta sit below 3:1 on white by design — the
 * legend, axis tooltip and "View Data" table are the mitigation.
 */
export const CHART_SERIES_COLORS = {
    light: ["#9452A8", "#1baf7a", "#eda100", "#e34948", "#2a78d6", "#008300", "#e87ba4", "#eb6834"],
    dark: ["#A873BB", "#199e70", "#c98500", "#e66767", "#3987e5", "#008300", "#d55181", "#d95926"]
} as const;

export interface ISeriesStyle {
    color: string;
    lineType: "solid" | "dashed";
}

/**
 * Assigns a series its slot colour in fixed order — never a bare cycle.
 * Beyond the 8 designed slots the hues repeat with a dashed line, so two
 * series never differ by colour memory alone.
 *
 * Args:
 *     seriesIdx (number): zero-based series index.
 *     darkMode (boolean): whether the dark palette applies.
 *
 * Returns:
 *     ISeriesStyle: the slot colour and line type for the series.
 */
export const seriesStyle = (seriesIdx: number, darkMode: boolean): ISeriesStyle => {
    const palette = darkMode ? CHART_SERIES_COLORS.dark : CHART_SERIES_COLORS.light;

    return {
        color: palette[seriesIdx % palette.length],
        lineType: seriesIdx < palette.length ? "solid" : "dashed"
    };
};

export interface IChartChrome {
    /** Chart + grid background — transparent so the card surface shows through. */
    background: string;
    /** Horizontal split lines (recessive hairlines). */
    gridLine: string;
    /** Axis baselines and ticks. */
    axisLine: string;
    /** Axis tick labels (muted ink, >= 4.5:1 on its surface). */
    axisLabel: string;
    /** Axis names and legend text (secondary ink). */
    ink: string;
}

/**
 * Plot chrome mapped onto the FLIP palette tokens (tailwind.config.js) so the
 * chart sits on the card surface instead of painting its own off-token panel.
 *
 * Args:
 *     darkMode (boolean): whether the dark tokens apply.
 *
 * Returns:
 *     IChartChrome: background, line and ink colours for the mode.
 */
export const chartChrome = (darkMode: boolean): IChartChrome => darkMode
    ? {
        background: "transparent",
        gridLine: "#2A2336", // dark-border
        axisLine: "#3A3147", // dark-border-strong
        axisLabel: "#9CA3AF", // gray-400 (dark fg-3)
        ink: "#D1D5DB" // gray-300 (dark fg-2)
    }
    : {
        background: "transparent",
        gridLine: "#E5E7EB", // gray-200
        axisLine: "#D1D5DB", // gray-300
        axisLabel: "#6B7280", // gray-500 — 4.8:1 on white
        ink: "#374151" // gray-700
    };
