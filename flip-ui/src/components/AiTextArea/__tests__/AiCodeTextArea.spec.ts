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



import { mountComponent } from "@test/helper";
import { createPinia, setActivePinia } from "pinia";
import { expect } from "vitest";

import { useSiteSettings } from "@/store/siteSettingsStore";

import AiCodeTextArea from "../AiCodeTextArea.vue";

describe("Ai Code TextArea", () => {
    test("Renders Component", () => {
        const comp = mountComponent(AiCodeTextArea, {
            props: {
                name: "test-code-textarea",
                label: "Test Code Label"
            },
            global: { plugins: [createPinia()] }
        });

        expect(comp.exists()).toBe(true);
        expect(comp.find("label").text()).toBe("Test Code Label");
    });

    test("mounts the CodeMirror editor from its own import", () => {
        const comp = mountComponent(AiCodeTextArea, {
            props: {
                name: "test-code-textarea",
                label: "Test Code Label"
            },
            global: { plugins: [createPinia()] }
        });

        // Global registration in main.ts never reaches unit tests (or lazy
        // chunks) — the component must import the editor itself.
        expect(comp.find(".CodeMirror").exists()).toBe(true);
    });

    test("applies the dracula theme in dark mode", () => {
        const pinia = createPinia();
        setActivePinia(pinia);
        useSiteSettings().darkMode = true;

        const comp = mountComponent(AiCodeTextArea, {
            props: {
                name: "test-code-textarea",
                label: "Test Code Label"
            },
            global: { plugins: [pinia] }
        });

        expect(comp.find(".CodeMirror").classes()).toContain("cm-s-flip-dark");
    });

    test("blocks focus entirely when readonly so mobile taps can't summon a caret", () => {
        const comp = mountComponent(AiCodeTextArea, {
            props: {
                name: "test-code-textarea",
                label: "Test Code Label",
                inputProps: { readonly: true }
            },
            global: { plugins: [createPinia()] }
        });

        // CodeMirror 5 exposes its instance on the wrapper element. readOnly
        // "nocursor" (unlike plain true) refuses focus, so tapping the locked
        // query on mobile shows no flickering caret and opens no keyboard.
        const cm = (comp.find(".CodeMirror").element as HTMLElement & {
            CodeMirror: { getOption: (o: string) => unknown };
        }).CodeMirror;
        expect(cm.getOption("readOnly")).toBe("nocursor");
    });
});
