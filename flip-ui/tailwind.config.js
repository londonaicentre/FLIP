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
                lightgreen: {
                    100: "#DCEDC8",
                    900: "#33691E",
                },
                deeporange: { 900: "#BF360C" },
                green: colors.emerald,
                // FLIP dark-mode palette (warm plum-charcoal). Single source of
                // truth for dark surfaces/borders — reference via `dark:bg-dark-*`
                // and `dark:border-dark-*`. Dark-mode text maps onto the standard
                // gray ramp (fg-1 gray-100, fg-2 gray-300, fg-3 gray-400, disabled
                // gray-500) and links onto primary-300/200, so those need no new
                // tokens. See the palette reference for usage rules.
                dark: {
                    canvas: "#13101A", // app background / page
                    surface: "#16121D", // cards, panels, sidebar
                    raised: "#201A29", // modals, popovers, sticky header, hover
                    inset: "#0E0B13", // input wells, code blocks, table headers
                    border: "#2A2336", // default dividers + card borders
                    "border-strong": "#3A3147", // input borders / separators with presence
                },
                // Status colours, lightened to read on dark surfaces. Render as
                // the solid colour for text + dot over a low-opacity tint.
                status: {
                    approved: "#4ADE80", // success / APPROVED
                    staged: "#F4BE1D", // warn / STAGED (brand gold)
                    unstaged: "#F87171", // danger / UNSTAGED
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
    variants: {
        extend: {},
    },
    plugins: [
        require('@tailwindcss/typography'),
        require('@tailwindcss/forms'),
    ]
}
