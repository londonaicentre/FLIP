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

import stylistic from "@stylistic/eslint-plugin";
import { defineConfigWithVueTs, vueTsConfigs } from "@vue/eslint-config-typescript";
import simpleImportSort from "eslint-plugin-simple-import-sort";
import pluginVue from "eslint-plugin-vue";

export default defineConfigWithVueTs(
    {
        name: "flip-ui/ignores",
        ignores: [
            "**/screenshotter.ts",
            "**/*.js",
            "**/test/ui/**",
            "dist/**",
            "coverage/**",
            "public/**",
            "node_modules/**",
        ],
    },

    pluginVue.configs["flat/recommended"],
    vueTsConfigs.recommended,

    {
        name: "flip-ui/globals",
        languageOptions: {
            globals: {
                defineProps: "readonly",
                withDefaults: "readonly",
                defineEmits: "readonly",
            },
        },
    },

    {
        name: "flip-ui/rules",
        plugins: {
            "simple-import-sort": simpleImportSort,
            "@stylistic": stylistic,
        },
        rules: {
            "simple-import-sort/imports": "error",
            "@stylistic/indent": ["error", 4, { SwitchCase: 1 }],
            "comma-spacing": ["error", { before: false, after: true }],
            "object-curly-spacing": ["error", "always"],
            "object-curly-newline": ["error", {
                ObjectExpression: { multiline: true, minProperties: 2 },
                ImportDeclaration: "never",
            }],
            "object-property-newline": ["error", { allowAllPropertiesOnSameLine: false }],
            "@typescript-eslint/no-explicit-any": "error",
            semi: ["error", "always"],
            "no-trailing-spaces": "error",
            "comma-dangle": "error",
            "max-len": ["warn", {
                tabWidth: 4,
                code: 120,
                ignoreUrls: true,
                ignorePattern: "(class=\"([\\s\\S]*?)\")|(d=\"([\\s\\S]*?)\")|(=\"([\\s\\S]*?)\")|(@apply\\s([^;]*);)",
                ignoreStrings: true,
                ignoreTemplateLiterals: true,
            }],
            quotes: ["error", "double"],
            "padding-line-between-statements": ["error", {
                blankLine: "always",
                prev: "*",
                next: "return",
            }],
            "vue/html-indent": ["error", 4],
            "vue/max-attributes-per-line": ["error", {
                singleline: 4,
                multiline: 1,
            }],
            "no-console": process.env.NODE_ENV === "production" ? "warn" : "off",
            "no-debugger": process.env.NODE_ENV === "production" ? "warn" : "off",
            "@typescript-eslint/no-empty-object-type": ["error", { allowInterfaces: "with-single-extends" }],
            "vue/html-closing-bracket-newline": ["error", {
                singleline: "never",
                multiline: "always",
            }],
        },
    },

    {
        name: "flip-ui/pages",
        files: ["src/pages/**/*.vue", "src/layouts/**/*.vue"],
        rules: {
            "vue/multi-word-component-names": "off",
        },
    },

    {
        name: "flip-ui/legacy-single-word-components",
        // Grandfathered single-word component names that predate the multi-word rule.
        // Renaming them ripples across imports and templates app-wide, so the exception is
        // scoped to these specific files; the rule still applies to every other partial.
        files: [
            "src/partials/models/Timeline.vue",
            "src/partials/models/Training.vue",
            "src/partials/projects/Filter.vue",
        ],
        rules: {
            "vue/multi-word-component-names": "off",
        },
    },

    {
        name: "flip-ui/tests",
        files: [
            "**/__tests__/**/*.{js,ts,jsx,tsx}",
            "**/tests/unit/**/*.spec.{js,ts,jsx,tsx}",
        ],
        languageOptions: {
            globals: {
                jest: "readonly",
                describe: "readonly",
                it: "readonly",
                test: "readonly",
                expect: "readonly",
                beforeAll: "readonly",
                beforeEach: "readonly",
                afterAll: "readonly",
                afterEach: "readonly",
            },
        },
    },
);
