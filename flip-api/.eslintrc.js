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

module.exports = {
    root: true,
    ignorePatterns: ["**/screenshotter.ts", "**/*.js", "**/test/ui/*"],
    env: { node: true },
    globals: {
        defineProps: "readonly",
        withDefaults: "readonly",
        defineEmits: "readonly"
    },
    extends: [
        "plugin:vue/vue3-essential",
        "eslint:recommended",
        "@vue/typescript/recommended",
        "plugin:vue/vue3-recommended",
    ],
    parser: "vue-eslint-parser",
    "plugins": ["@typescript-eslint", "simple-import-sort"],
    parserOptions: {
        ecmaVersion: 2020,
        "parser": "@typescript-eslint/parser"
    },
    rules: {
        "vue/script-setup-uses-vars": 'error',
        "simple-import-sort/imports": "error",
        "object-curly-spacing": ["error", "always"],
        "object-curly-newline": ["error", {
            "ObjectExpression": {
                "multiline": true,
                "minProperties": 2
            },
            "ImportDeclaration": "never",
        }],
        "object-property-newline": ["error", { allowAllPropertiesOnSameLine: false }],
        "@typescript-eslint/no-explicit-any": "error",
        semi: ["error", "always"],
        "no-trailing-spaces": "error",
        "comma-dangle": "error",
        "max-len": [
            "warn",
            {
                tabWidth: 4,
                code: 120,
                ignoreUrls: true,
                ignorePattern:
                    "(class=\"([\\s\\S]*?)\")|(d=\"([\\s\\S]*?)\")|(=\"([\\s\\S]*?)\")|(@apply\s([^;]*);)",
                ignoreStrings: true,
                ignoreTemplateLiterals: true
            },
        ],
        quotes: ["error", "double"],
        "padding-line-between-statements": [
            "error",
            {
                blankLine: "always",
                prev: "*",
                next: "return"
            },
        ],
        "@typescript-eslint/indent": ["error", 4, { "SwitchCase": 1, }],
        "vue/html-indent": ["error", 4],
        "vue/max-attributes-per-line": [
            "error",
            {
                singleline: 4,
                multiline: 1,
            },
        ],
        "no-console": process.env.NODE_ENV === "production" ? "warn" : "off",
        "no-debugger": process.env.NODE_ENV === "production" ? "warn" : "off",
        "@typescript-eslint/no-empty-interface": [
            "error",
            { allowSingleExtends: true, },
        ],
        "vue/html-closing-bracket-newline": [
            "error",
            {
                singleline: "never",
                multiline: "always",
            },
        ],
    },
    overrides: [
        {
            files: [
                "**/__tests__/*.{j,t}s?(x)",
                "**/tests/unit/**/*.spec.{j,t}s?(x)",
            ],
            env: { jest: true, },
        },
    ],
};
