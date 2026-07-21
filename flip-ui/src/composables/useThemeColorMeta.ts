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



import { Ref, watch } from "vue";

// Page-background tokens from tailwind.config.js (theme.colors.body / theme.colors.dark.canvas);
// useThemeColorMeta.spec.ts asserts they match, so a palette change fails tests instead of
// silently desynchronising the browser-chrome tint from the page background.
export const THEME_COLOR_LIGHT = "#FAF7F5";
export const THEME_COLOR_DARK = "#0B1018";

// Keeps <meta name="theme-color"> in sync with the app theme so the browser chrome (status/URL
// bar) matches the page background instead of defaulting to black in dark mode. The app theme is
// class-driven (useDark, user-toggleable) and can disagree with the OS colour scheme, so static
// prefers-color-scheme media variants of the meta tag would tint the chrome wrongly.
export default function useThemeColorMeta(isDark: Ref<boolean>): void {
    watch(isDark,
        (dark) => {
            let meta = document.head.querySelector<HTMLMetaElement>("meta[name=\"theme-color\"]");

            if (!meta) {
                meta = document.createElement("meta");
                meta.name = "theme-color";
                document.head.appendChild(meta);
            }

            meta.content = dark ? THEME_COLOR_DARK : THEME_COLOR_LIGHT;
        },
        { immediate: true }
    );
}
