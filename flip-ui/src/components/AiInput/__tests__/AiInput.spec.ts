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



import { createTestingPinia } from "@pinia/testing";
import { mount } from "@vue/test-utils";
import { expect, it, vi } from "vitest";

import * as helpers from "@/utils/helpers";

import AiInput from "../AiInput.vue";

describe("Ai Input", () => {
    it("Renders Component", () => {
        vi.spyOn(helpers, "getRandomId").mockImplementationOnce(() => "random-id");

        const comp = mount(AiInput, {
            props: { name: "something" },
            global: { 
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })]
            }
        });

        // Test functionality instead of snapshots
        expect(comp.exists()).toBe(true);
        expect(comp.find('input').attributes('name')).toBe('something');
    });
});
