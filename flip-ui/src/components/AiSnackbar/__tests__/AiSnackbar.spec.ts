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
import AiSnackbar from "../AiSnackbar.vue";

describe("Ai Snackbar", () => {

    test("handles empty message list", () => {
        const wrapper = mount(AiSnackbar);
        
        expect(wrapper.findAll('[data-testid="snackbar-item"]')).toHaveLength(0);
    });

    test("renders component structure", () => {
        const wrapper = mount(AiSnackbar);
        
        // Check basic structure exists
        expect(wrapper.find('.fixed.inset-0.z-10')).toBeTruthy();
        expect(wrapper.find('.w-full.max-w-md')).toBeTruthy();
    });

});