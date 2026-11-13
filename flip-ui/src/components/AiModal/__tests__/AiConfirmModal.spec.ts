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




import { flushPromises, mount } from "@vue/test-utils";

import AiConfirmModal from "../AiConfirmModal.vue";

describe("Ai ConfirmModal", () => {
    function mountWithTyping(typingConfirmation: string) {
        return mount(AiConfirmModal, {
            attachTo: document.body,
            props: {
                dialog: true,
                continueAction: () => {},
                typingConfirmation
            }
        });
    }

    async function schemaFor(typingConfirmation: string) {
        const wrapper = mountWithTyping(typingConfirmation);
        await flushPromises();
        const { schema } = wrapper.vm as unknown as { schema: { validate: (v: unknown) => Promise<unknown> } };

        return {
            wrapper,
            schema
        };
    }

    test("Renders Component", () => {
        const comp = mount(AiConfirmModal);

        expect(comp.element).toMatchSnapshot();
    });

    describe("schema (exposed via defineExpose)", () => {
        // The component exposes its yup schema for testing because
        // HeadlessUI's Dialog teleports content out of the SFC tree, so
        // vee-validate's validation can't be driven through wrapper.find()
        // in jsdom. Exercising the schema directly covers the same
        // confirmation-match codepath the form would.

        test("rejects empty input when typingConfirmation is set", async () => {
            const { wrapper, schema } = await schemaFor("DELETE");
            await expect(schema.validate({ confirmation: "" })).rejects.toThrow();
            wrapper.unmount();
        });

        test("rejects undefined input (first validation pass)", async () => {
            const { wrapper, schema } = await schemaFor("DELETE");
            // Yup runs the validator before the user types; the schema
            // must tolerate `confirmation: undefined` without throwing.
            await expect(schema.validate({ confirmation: undefined })).rejects.toThrow();
            wrapper.unmount();
        });

        test("accepts a case-insensitive match", async () => {
            const { wrapper, schema } = await schemaFor("DELETE");
            await expect(schema.validate({ confirmation: "delete" })).resolves.toEqual({ confirmation: "delete" });
            wrapper.unmount();
        });

        test("accepts any input when typingConfirmation is empty (no confirmation required)", async () => {
            const { wrapper, schema } = await schemaFor("");
            await expect(schema.validate({ confirmation: undefined })).resolves.toEqual({});
            await expect(schema.validate({ confirmation: "anything" })).resolves.toEqual({ confirmation: "anything" });
            wrapper.unmount();
        });
    });

    describe("typing-confirmation validator (no-throw guarantee)", () => {
        // Invariant: mounting the component with a non-empty
        // `typingConfirmation` must not throw, even though
        // `this.parent.confirmation` is undefined on the first yup pass.
        // The headless DOM here can't fully simulate HeadlessUI's teleport,
        // so we rely on mount + validation-pass to reach the schema's
        // test() callback without crashing.

        test("does not throw on subsequent prop changes that re-trigger validation", async () => {
            const wrapper = mountWithTyping("");
            await flushPromises();

            await wrapper.setProps({ typingConfirmation: "ALPHA" });
            await flushPromises();

            await wrapper.setProps({ typingConfirmation: "BETA" });
            await flushPromises();

            expect(wrapper.exists()).toBe(true);

            wrapper.unmount();
        });
    });

    test("escapes HTML in confirmationText to prevent XSS", () => {
        const payload = "<img src=x onerror=alert(1)>";

        const comp = mount(AiConfirmModal, {
            global: { renderStubDefaultSlot: true },
            props: {
                dialog: true,
                continueAction: () => {},
                confirmationText: payload
            }
        });

        const html = comp.html();

        expect(html).not.toContain("<img");
        expect(html).toContain("&lt;img");

        comp.unmount();
    });

    test("renders the confirmation slot when provided", () => {
        const comp = mount(AiConfirmModal, {
            global: { renderStubDefaultSlot: true },
            props: {
                dialog: true,
                continueAction: () => {}
            },
            slots: { confirmation: "<strong data-test=\"slot-marker\">slot content</strong>" }
        });

        const html = comp.html();

        expect(html).toContain("data-test=\"slot-marker\"");
        expect(html).toContain("slot content");

        comp.unmount();
    });
});
