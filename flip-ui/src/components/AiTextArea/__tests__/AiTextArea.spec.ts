/*
 * Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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
import { expect } from "vitest";

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
        expect(comp.find('textarea').exists()).toBe(true);
        expect(comp.find('label').text()).toBe('Test Label');
    });
});
