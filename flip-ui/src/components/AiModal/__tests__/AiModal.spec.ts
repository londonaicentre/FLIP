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




import { mount } from "@vue/test-utils";
import { vi } from "vitest";

import AiModal from "../AiModal.vue";

describe("AiModal", () => {
    test("Renders Component", () => {
        const comp = mount(AiModal);

        expect(comp.element).toMatchSnapshot();
    });

    const mountOpen = (props = {}) => mount(AiModal, {
        props: {
            dialog: true,
            ...props
        },
        slots: { default: "<p>Body copy</p>" },
        global: { renderStubDefaultSlot: true }
    });

    test("shows the title, description and slotted body", () => {
        const text = mountOpen({
            title: "Delete model",
            description: "This cannot be undone"
        }).text();

        expect(text).toContain("Delete model");
        expect(text).toContain("This cannot be undone");
        expect(text).toContain("Body copy");
    });

    test("emits closeModal(false) from the Close button", async () => {
        const comp = mountOpen();

        await comp.find("[data-test=close-modal-btn]").trigger("click");

        expect(comp.emitted("closeModal")).toEqual([[false]]);
    });

    test("offers no continue button without a continue action", () => {
        expect(mountOpen().find("[data-test=submit-modal-btn]").exists()).toBe(false);
    });

    test("runs the continue action once from the labelled continue button", async () => {
        const continueAction = vi.fn();
        const comp = mountOpen({
            continueAction,
            continueButtonText: "Delete"
        });
        const btn = comp.find("[data-test=submit-modal-btn]");

        expect(btn.text()).toBe("Delete");
        await btn.trigger("click");
        await btn.trigger("click");

        expect(continueAction).toHaveBeenCalledTimes(1);
    });

    test("disables the continue button on request", () => {
        const comp = mountOpen({
            continueAction: vi.fn(),
            disabledContinueButton: true
        });

        expect(comp.find("[data-test=submit-modal-btn]").attributes("disabled")).toBeDefined();
    });

    test("drops the footer buttons in basic mode", () => {
        const comp = mountOpen({
            basic: true,
            continueAction: vi.fn()
        });

        expect(comp.find("[data-test=close-modal-btn]").exists()).toBe(false);
        expect(comp.find("[data-test=submit-modal-btn]").exists()).toBe(false);
    });
});
