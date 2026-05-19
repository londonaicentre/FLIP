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
import type { FieldEntry } from "vee-validate";
import { nextTick } from "vue";

import { IOption } from "@/components/AiSelect/interfaces";

import AiChipSelect from "../AiChipSelect.vue";

describe("AI Chip Select", () => {
    const component = mountComponent(AiChipSelect);

    it("renders the component successfully", () => {
        expect(component.element).toMatchSnapshot();
    });
});

describe("AI Chip Select — script logic", () => {
    const optionA: IOption = { id: "1", description: "Admin" };
    const optionB: IOption = { id: "2", description: "Researcher" };

    const fieldEntry = (option: IOption, key = option.id): FieldEntry =>
        ({ key, value: option, isFirst: false, isLast: false } as FieldEntry);

    it("emits push + validate when a new option is selected", async () => {
        const component = mountComponent(AiChipSelect, {
            props: { options: [optionA, optionB], selectedOptions: [], defaultText: "Select role" },
        });

        // Drive the internal ref directly. Headlessui Listbox doesn't open in
        // jsdom so we can't click ListboxOption — but the script under test
        // is the watcher on currentlySelected, which fires regardless of
        // which path set the ref. Tests the emit contract, not Listbox
        // internals. Vue exposes script-setup state via $.setupState.
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (component.vm as any).$.setupState.currentlySelected = optionA;
        await nextTick();

        expect(component.emitted("push")).toEqual([[optionA]]);
        expect(component.emitted("validate")).toEqual([[]]);
    });

    it("skips push but still fires validate when re-selecting an already-selected option", async () => {
        const component = mountComponent(AiChipSelect, {
            props: {
                options: [optionA, optionB],
                selectedOptions: [fieldEntry(optionA)],
                defaultText: "Select role",
            },
        });

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (component.vm as any).$.setupState.currentlySelected = optionA;
        await nextTick();

        expect(component.emitted("push")).toBeUndefined();
        expect(component.emitted("validate")).toEqual([[]]);
    });

    it("renders a chip for each selectedOption with the option description", () => {
        const component = mountComponent(AiChipSelect, {
            props: {
                options: [optionA, optionB],
                selectedOptions: [fieldEntry(optionA), fieldEntry(optionB)],
                defaultText: "Select role",
            },
        });

        const chips = component.findAllComponents({ name: "AiButton" });
        expect(chips).toHaveLength(2);
        expect(chips[0].text()).toContain("Admin");
        expect(chips[1].text()).toContain("Researcher");
    });

    it("renders a checkmark on already-selected dropdown options", async () => {
        const component = mountComponent(AiChipSelect, {
            attachTo: document.body,
            props: {
                options: [optionA, optionB],
                selectedOptions: [fieldEntry(optionA)],
                defaultText: "Select role",
            },
        });

        // Open the Listbox so the v-for over options actually renders, which
        // is the only path that exercises selectedOptionsInclude(). Headlessui
        // requires a real click on its ListboxButton; trigger() on the
        // wrapper element does the job in jsdom once attachTo is set.
        await component.get('[aria-haspopup="listbox"]').trigger("click");
        await nextTick();

        const optionElements = component.findAll('[data-test="chip-select-option"]');
        expect(optionElements).toHaveLength(2);
        // The check icon renders as an inline <svg> only when
        // selectedOptionsInclude returns true. optionA is in selectedOptions
        // → svg present; optionB isn't → no svg.
        expect(optionElements[0].find("svg").exists()).toBe(true);
        expect(optionElements[1].find("svg").exists()).toBe(false);

        component.unmount();
    });

    it("emits remove + validate with the chip index when a chip is clicked", async () => {
        const component = mountComponent(AiChipSelect, {
            props: {
                options: [optionA, optionB],
                selectedOptions: [fieldEntry(optionA), fieldEntry(optionB)],
                defaultText: "Select role",
            },
        });

        // The chips render via AiButton (a <button> wrapper) and the @click
        // attaches as a fall-through listener on that inner button. Trigger
        // the DOM element directly so @vue/test-utils dispatches a real
        // click rather than emitting a synthetic component event.
        const chipButtons = component.findAll(".flex.flex-wrap button");
        expect(chipButtons).toHaveLength(2);
        await chipButtons[1].trigger("click");

        expect(component.emitted("remove")).toEqual([[1]]);
        expect(component.emitted("validate")).toEqual([[]]);
    });

    it("clears currentlySelected when the chip removed corresponds to the dropdown's selection", async () => {
        const component = mountComponent(AiChipSelect, {
            props: {
                options: [optionA, optionB],
                selectedOptions: [fieldEntry(optionA)],
                defaultText: "Select role",
            },
        });

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const setupState = (component.vm as any).$.setupState;
        setupState.currentlySelected = optionA;
        await nextTick();

        // Click the inner <button> so the fall-through @click on AiButton
        // actually fires (triggering on the component wrapper would only
        // emit a synthetic Vue event, not invoke the listener).
        await component.find(".flex.flex-wrap button").trigger("click");

        // Clearing prevents the next watcher firing if the same option is
        // re-selected from the dropdown — without it, the user would have
        // to pick a different option in between to "reset" the chip.
        expect(setupState.currentlySelected).toBeUndefined();
        expect(component.emitted("remove")).toEqual([[0]]);
    });
});
