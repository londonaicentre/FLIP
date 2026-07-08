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
import { createPinia } from "pinia";
import { describe, expect, it } from "vitest";

import AiAlert from "@/components/AiAlert/AiAlert.vue";

describe("Ai Alert", () => {
    it("Renders Component", () => {

        const comp = mount(AiAlert, {
            global: { plugins: [createPinia()] },
            props: {
                variant: "success",
                text: "Testing"
            }
        });

        expect(comp.element).toMatchSnapshot();
    });

    it("escapes HTML in the text prop to prevent XSS", () => {
        const payload = "<img src=x onerror=alert(1)>";

        const comp = mount(AiAlert, {
            global: { plugins: [createPinia()] },
            props: {
                variant: "info",
                text: payload
            }
        });

        expect(comp.html()).not.toContain("<img");
        expect(comp.text()).toContain(payload);
    });

    it("renders the default slot when provided", () => {
        const comp = mount(AiAlert, {
            global: { plugins: [createPinia()] },
            props: { variant: "info" },
            slots: { default: "<strong data-test=\"slot-marker\">authored content</strong>" }
        });

        const html = comp.html();

        expect(html).toContain("data-test=\"slot-marker\"");
        expect(html).toContain("<strong");
        expect(html).toContain("authored content");
    });

    it("emits action when the action label is clicked", async () => {
        const comp = mount(AiAlert, {
            global: { plugins: [createPinia()] },
            props: {
                variant: "info",
                text: "Testing",
                actionText: "Retry"
            }
        });

        await comp.find("span.font-black").trigger("click");

        expect(comp.emitted("action")).toHaveLength(1);
    });

    it("hides itself when the close button is clicked", async () => {
        const comp = mount(AiAlert, {
            global: { plugins: [createPinia()] },
            props: {
                variant: "info",
                text: "Testing",
                close: true
            }
        });

        expect(comp.text()).toContain("Testing");
        await comp.find("button").trigger("click");

        expect(comp.find("button").exists()).toBe(false);
        expect(comp.text()).not.toContain("Testing");
    });
});
