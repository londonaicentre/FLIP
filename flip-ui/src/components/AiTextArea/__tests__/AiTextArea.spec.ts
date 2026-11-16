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
import { Form } from "vee-validate";
import { expect, vi } from "vitest";
import { h } from "vue";
import { object, string } from "yup";

import AiTextArea from "../AiTextArea.vue";

describe("Ai TextArea", () => {
    test("Renders Component", () => {
        const comp = mount(AiTextArea, {
            props: {
                name: "test-textarea",
                label: "Test Label"
            }
        });

        // Test functionality instead of snapshots
        expect(comp.exists()).toBe(true);
        expect(comp.find("textarea").exists()).toBe(true);
        expect(comp.find("label").text()).toBe("Test Label");
    });

    test("starts from the initial value and shows the hint and required marker", () => {
        const comp = mount(AiTextArea, {
            props: {
                name: "description",
                label: "Description",
                initialValue: "Existing text",
                hint: "Up to 250 characters",
                required: true
            }
        });

        expect(comp.find("textarea").element.value).toBe("Existing text");
        expect(comp.text()).toContain("Up to 250 characters");
        expect(comp.find("label span").exists()).toBe(true);
    });

    test("renders the footer slot only when the footer is enabled", () => {
        const slots = { footer: "<span data-test='footer'>Footer</span>" };
        const props = {
            name: "description",
            label: "Description"
        };

        expect(mount(AiTextArea, {
            props,
            slots
        }).find("[data-test=footer]").exists()).toBe(false);
        expect(mount(AiTextArea, {
            props: {
                ...props,
                footer: true
            },
            slots
        }).find("[data-test=footer]").exists()).toBe(true);
    });

    test("replaces the hint with the form's validation error once the field is emptied", async () => {
        const schema = object({ description: string().required("A description is required") });
        const comp = mount(Form, {
            props: { validationSchema: schema },
            slots: {
                default: () => h(AiTextArea, {
                    name: "description",
                    label: "Description",
                    hint: "Up to 250 characters",
                    initialValue: "Existing text"
                })
            }
        });
        const textarea = comp.find("textarea");

        await textarea.setValue("");
        await textarea.trigger("blur");

        await vi.waitFor(() => expect(comp.find(".error_message").text()).toBe("A description is required"));
        expect(comp.text()).not.toContain("Up to 250 characters");
        expect(comp.find("label").classes()).toContain("text-red-500");
    });
});
