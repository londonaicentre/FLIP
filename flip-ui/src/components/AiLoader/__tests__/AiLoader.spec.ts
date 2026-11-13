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
import { describe, expect, it } from "vitest";

import AiLoader from "../AiLoader.vue";

describe("AiLoader", () => {
    it("renders a standard primary spinner with no message by default", () => {
        const wrapper = mount(AiLoader);

        expect(wrapper.find("svg").classes())
            .toEqual(expect.arrayContaining(["animate-spin", "w-6", "text-primary-600"]));
        expect(wrapper.find("div.text-center").exists()).toBe(false);
    });

    it("shows the message and honours the small and inverted variants", () => {
        const wrapper = mount(AiLoader, {
            props: {
                message: "Loading trusts",
                small: true,
                inverted: true
            }
        });

        expect(wrapper.text()).toBe("Loading trusts");
        expect(wrapper.find("svg").classes()).toEqual(expect.arrayContaining(["w-4", "text-gray-200"]));
        expect(wrapper.find("svg").classes()).not.toContain("w-6");
    });
});
