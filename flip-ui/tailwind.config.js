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
                body: colors.gray[100],
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
                green: colors.emerald
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
        require('@tailwindcss/line-clamp')
    ]
}
