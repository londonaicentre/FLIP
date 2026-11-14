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


const colors = require('tailwindcss/colors');

module.exports = {
    content: ['./index.html', './src/**/*.{vue,js,ts,jsx,tsx}'],
    darkMode: "class",
    important: true,
    theme: {
        extend: {
            fontSize: {
                // Secondary text step: metadata lines under a title, table sub-lines.
                note: "13px",
                sm: "0.95rem"
            },
            fontFamily: {
                heading: "Bai Jamjuree",
                sans: "Inter",
                mono: "JetBrainsMono"
            },
            colors: {
                // Add new colours here
                // `body` is the application-wide page background. Matches the
                // design handoff's `--paper` token (#FAF7F5), a warm off-white
                // with a pink undertone that complements the primary palette.
                body: "#FAF7F5",
                // Design-handoff ink tokens (04·A ProjectChrome): `ink` is the
                // warm near-black text ink, `ink-3` its muted step. Ghost tab
                // navs use them for text and (at low alpha) the active fill.
                ink: {
                    DEFAULT: "#1A1320",
                    3: "#7A727F",
                },
                primary: {
                    100: "#F7F3F9",
                    200: "#DBC4E2",
                    300: "#B88AC6",
                    400: "#9452A8",
                    500: "#61366e",
                    600: "#482852",
                    700: "#301A37",
                    800: "#180D1B",
                    900: "#040205",
                },
                // Steel blue — the data/infrastructure accent from the FLIP design system
                // ("OMOP only" project-type chip, FLIP#1071).
                steel: {
                    100: "#EBF0F5",
                    700: "#2C3E6B",
                },
                lightgreen: {
                    100: "#DCEDC8",
                    900: "#33691E",
                },
                deeporange: { 900: "#BF360C" },
                green: colors.emerald,
                // FLIP dark-mode palette ("True graphite": neutral, deep, high contrast). Single source of
                // truth for dark surfaces/borders — reference via `dark:bg-dark-*`
                // and `dark:border-dark-*`. Dark-mode text maps onto the standard
                // gray ramp (fg-1 gray-100, fg-2 gray-300, fg-3 gray-400, disabled
                // gray-500) and links onto primary-300/200, so those need no new
                // tokens. See the palette reference for usage rules.
                dark: {
                    canvas: "#08080A", // app background / page (near-black graphite)
                    surface: "#141417", // cards, panels, sidebar: lighter than canvas so cards read as raised
                    raised: "#1E1E23", // modals, popovers, sticky header, hover
                    inset: "#050506", // input wells, code blocks, table headers (darker than surface so wells read)
                    border: "#2E2E35", // default dividers + card borders
                    "border-strong": "#6B6B72", // input borders / separators with presence (3:1 on raised)
                },
                // Status colours, lightened to read on dark surfaces. Render as
                // the solid colour for text + dot over a low-opacity tint.
                status: {
                    approved: "#34D399", // APPROVED (emerald-400; light mode uses emerald)
                    staged: "#FBBF24", // STAGED (amber-400; light mode uses amber)
                    unstaged: "#9CA3AF", // UNSTAGED / draft (gray-400): grey in both themes, never the error red
                    info: "#A9C5DC", // info / data
                }
            },
            screens: {
                "3xl": "1920px",
                "4xl": "2560px",
                "5xl": "3840px"
            }
        },
    },
    plugins: [
        require('@tailwindcss/forms'),
    ]
}
