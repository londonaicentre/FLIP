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
import { flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { expect } from "vitest";

import { useSiteSettings } from "@/store/siteSettingsStore";

import AiCodeTextArea from "../AiCodeTextArea.vue";

// CodeMirror measures rendered text through Range#getBoundingClientRect /
// getClientRects, which jsdom does not implement — any mount with non-empty
// content throws without these zero-rect polyfills.
beforeAll(() => {
    const zeroRect = {
        x: 0,
        y: 0,
        top: 0,
        bottom: 0,
        left: 0,
        right: 0,
        width: 0,
        height: 0,
        toJSON: () => ({})
    } as DOMRect;
    Range.prototype.getBoundingClientRect = () => zeroRect;
    Range.prototype.getClientRects = () =>
        ({
            length: 0,
            item: () => null,
            [Symbol.iterator]: [].values
        } as unknown as DOMRectList);
});

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

    test("applies the flip-dark theme in dark mode", () => {
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

    test("shows no copy button unless asked for one", () => {
        const comp = mountComponent(AiCodeTextArea, {
            props: {
                name: "test-code-textarea",
                label: "Test Code Label"
            },
            global: { plugins: [createPinia()] }
        });

        expect(comp.find("[data-test=copy-code-btn]").exists()).toBe(false);
    });

    test("copies the editor content to the clipboard from the overlay button", async () => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        Object.defineProperty(navigator, "clipboard", {
            value: { writeText },
            configurable: true
        });

        const comp = mountComponent(AiCodeTextArea, {
            props: {
                name: "test-code-textarea",
                label: "Test Code Label",
                copyable: true,
                initialValue: "SELECT person_id FROM person;"
            },
            global: { plugins: [createPinia()] }
        });

        await comp.find("[data-test=copy-code-btn]").trigger("click");

        expect(writeText).toHaveBeenCalledWith("SELECT person_id FROM person;");
    });

    test("swallows a clipboard failure and stays in its idle state", async () => {
        const writeText = vi.fn().mockRejectedValue(new Error("denied"));
        Object.defineProperty(navigator, "clipboard", {
            value: { writeText },
            configurable: true
        });

        const comp = mountComponent(AiCodeTextArea, {
            props: {
                name: "test-code-textarea",
                label: "Test Code Label",
                copyable: true,
                initialValue: "SELECT person_id FROM person;"
            },
            global: { plugins: [createPinia()] }
        });

        const button = comp.find("[data-test=copy-code-btn]");
        await button.trigger("click");
        await flushPromises();

        // The rejected write is swallowed (no unhandled rejection from the click)
        // and the "copied" check feedback never shows — the button stays idle.
        expect(writeText).toHaveBeenCalledWith("SELECT person_id FROM person;");
        expect(button.attributes("aria-label")).toBe("Copy to clipboard");
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
